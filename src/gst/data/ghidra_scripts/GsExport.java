// Game Study Toolkit export: decompiled C grouped by namespace plus the data `gst index` reads.
// Args: <out dir> [max functions to decompile, default 0 = all] [seconds per function, default 0 = no limit]
//       [regex on qualified names to decompile, @file holding it, or -]
// Writes under <out dir>:
//   src/<namespace path>.c        decompiled functions of one class/namespace (global ones in src/_global/NNNN.c)
//   src/....c.map.json            per file: functions with their lines and call sites (exact, from the decompiler)
//   src/_listed/NNNN.c (+ map)    functions not decompiled this run, one line each, with their call edges
//   functions.jsonl calls.jsonl imports.jsonl strings.jsonl meta.json
// No caps: every defined string (streamed to strings.jsonl, full value, every referencing function), every field
// of a class layout; limits only when gst passes them (max functions, seconds per function).
// Addresses are RVAs (image base subtracted); names use '.' between namespaces (Sim::Step -> Sim.Step).
// @category GameStudy

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.regex.Pattern;

import ghidra.app.decompiler.*;
import ghidra.app.decompiler.component.DecompilerUtils;
import ghidra.app.decompiler.parallel.DecompilerCallback;
import ghidra.app.decompiler.parallel.ParallelDecompiler;
import ghidra.app.script.GhidraScript;
import ghidra.framework.Application;
import ghidra.framework.options.Options;
import ghidra.program.model.address.Address;
import ghidra.program.model.data.DataType;
import ghidra.program.model.data.DataTypeComponent;
import ghidra.program.model.data.StringDataInstance;
import ghidra.program.model.data.Structure;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.symbol.*;
import ghidra.util.task.TaskMonitor;

public class GsExport extends GhidraScript {

	private static final int CHUNK = 400;        // functions per file: global namespace and very large classes
	private static final int LISTED_CHUNK = 5000;

	private Address base;
	private String module;
	private Path out;
	private final Set<String> usedPaths = new HashSet<>();  // lower-case: Windows paths are case-insensitive
	private final List<String> callRows = new ArrayList<>();
	private final Map<Function, String[]> located = new HashMap<>();  // function -> {file, line}
	private final Map<String, String> imported = new HashMap<>();     // unique import name -> lib.Name

	static final class Decompiled {
		final Function function;
		final List<String> lines;
		final List<Object[]> calls;  // {Integer line within the function (1-based), String target}
		final boolean failed;

		Decompiled(Function function, List<String> lines, List<Object[]> calls, boolean failed) {
			this.function = function;
			this.lines = lines;
			this.calls = calls;
			this.failed = failed;
		}
	}

	@Override
	protected void run() throws Exception {
		long t0 = System.currentTimeMillis();
		String[] args = getScriptArgs();
		out = Paths.get(args[0]);
		int max = args.length > 1 ? Integer.parseInt(args[1]) : 0;
		if (max <= 0) {
			max = Integer.MAX_VALUE;  // 0 = every function
		}
		int timeout = args.length > 2 ? Integer.parseInt(args[2]) : 0;  // 0 = no limit per function
		String onlyArg = args.length > 3 ? args[3] : "-";
		if (onlyArg.startsWith("@")) {  // gst passes the regex in a file: cmd.exe would mangle | ^ & in arguments
			onlyArg = Files.readString(Paths.get(onlyArg.substring(1)), StandardCharsets.UTF_8).trim();
		}
		Pattern only = "-".equals(onlyArg) || onlyArg.isEmpty() ? null : Pattern.compile(onlyArg, Pattern.CASE_INSENSITIVE);
		base = currentProgram.getImageBase();
		module = currentProgram.getName();
		Files.createDirectories(out.resolve("src"));

		FunctionManager fm = currentProgram.getFunctionManager();
		Map<String, Integer> importCount = new HashMap<>();
		for (Function f : fm.getExternalFunctions()) {
			importCount.merge(f.getName(), 1, Integer::sum);
			imported.put(f.getName(), qname(f));
		}
		importCount.forEach((name, n) -> {
			if (n > 1) {
				imported.remove(name);  // same name in two libraries: keep the call by name
			}
		});
		List<Function> functions = new ArrayList<>();
		int thunks = 0;
		for (Function f : fm.getFunctions(true)) {
			if (f.isThunk()) {
				thunks++;
			}
			else {
				functions.add(f);
			}
		}
		List<Function> candidates = new ArrayList<>();
		for (Function f : functions) {
			if (only == null || only.matcher(qname(f)).find()) {
				candidates.add(f);
			}
		}
		// named functions (PDB, exports, RTTI) first, then by address
		candidates.sort(Comparator.comparing((Function f) -> f.getSymbol().getSource() == SourceType.DEFAULT)
			.thenComparing(Function::getEntryPoint));
		List<Function> chosen = candidates.subList(0, Math.min(max, candidates.size()));
		Set<Function> chosenSet = new HashSet<>(chosen);

		Map<Function, Decompiled> done = Collections.synchronizedMap(new HashMap<>());
		DecompilerCallback<Decompiled> callback = new DecompilerCallback<>(currentProgram, decompiler -> {
			DecompileOptions options = new DecompileOptions();
			options.grabFromProgram(currentProgram);
			decompiler.setOptions(options);
			decompiler.toggleCCode(true);
			decompiler.toggleSyntaxTree(true);
			decompiler.setSimplificationStyle("decompile");
		}) {
			@Override
			public Decompiled process(DecompileResults results, TaskMonitor m) {
				return render(results);
			}
		};
		callback.setTimeout(Math.max(0, timeout));  // the decompiler arms no timer for 0
		try {
			ParallelDecompiler.decompileFunctions(callback, currentProgram, chosen.iterator(),
				d -> done.put(d.function, d), monitor);
		}
		finally {
			callback.dispose();
		}

		// group by namespace, address order inside each file
		Map<String, List<Function>> groups = new TreeMap<>();
		for (Function f : chosen) {
			groups.computeIfAbsent(groupKey(f), k -> new ArrayList<>()).add(f);
		}
		int failed = 0;
		for (Map.Entry<String, List<Function>> e : groups.entrySet()) {
			List<Function> members = e.getValue();
			members.sort(Comparator.comparing(Function::getEntryPoint));
			for (int i = 0; i < members.size(); i += CHUNK) {
				List<Function> part = members.subList(i, Math.min(i + CHUNK, members.size()));
				failed += writeFile(filePath(e.getKey(), i / CHUNK), part, done);
			}
		}
		List<Function> listed = new ArrayList<>();
		for (Function f : functions) {
			if (!chosenSet.contains(f)) {
				listed.add(f);
			}
		}
		for (int i = 0; i < listed.size(); i += LISTED_CHUNK) {
			writeListed(String.format("src/_listed/%04d.c", i / LISTED_CHUNK + 1),
				listed.subList(i, Math.min(i + LISTED_CHUNK, listed.size())));
		}

		writeFunctions(functions);
		Files.write(out.resolve("calls.jsonl"), callRows, StandardCharsets.UTF_8);
		int imports = writeImports(fm);
		int strings = writeStrings();

		Options info = currentProgram.getOptions(Program.PROGRAM_INFO);
		Map<String, Object> meta = new LinkedHashMap<>();
		meta.put("module", module);
		meta.put("sha256", currentProgram.getExecutableSHA256());
		meta.put("image_base", "0x" + base.toString());
		meta.put("language", currentProgram.getLanguageID().toString());
		meta.put("compiler", currentProgram.getCompilerSpec().getCompilerSpecID().toString());
		meta.put("pdb_loaded", info.getBoolean("PDB Loaded", false));
		meta.put("pdb_file", info.getString("PDB File", null));
		meta.put("functions", functions.size());
		meta.put("decompiled", chosen.size() - failed);
		meta.put("failed", failed);
		meta.put("listed", listed.size());
		meta.put("thunks", thunks);
		meta.put("imports", imports);
		meta.put("strings", strings);
		meta.put("max_decompile", max);
		meta.put("only", only == null ? null : only.pattern());
		meta.put("ghidra", Application.getApplicationVersion());
		meta.put("seconds", (System.currentTimeMillis() - t0) / 1000);
		Files.writeString(out.resolve("meta.json"), json(meta) + "\n", StandardCharsets.UTF_8);
		println("gst: exported " + (chosen.size() - failed) + " of " + functions.size() + " functions to " + out);
	}

	private Decompiled render(DecompileResults results) {
		Function f = results.getFunction();
		ClangTokenGroup markup = results.getCCodeMarkup();
		if (markup == null) {
			String why = results.getErrorMessage() == null ? "" : results.getErrorMessage().trim().replace("*/", "* /");
			return new Decompiled(f, List.of("/* decompilation failed: " + why + " */"), List.of(), true);
		}
		List<String> lines = new ArrayList<>();
		List<Object[]> calls = new ArrayList<>();
		for (ClangLine line : DecompilerUtils.toLines(markup)) {
			StringBuilder sb = new StringBuilder(line.getIndentString());
			for (ClangToken token : line.getAllTokens()) {
				sb.append(token.getText());
				if (token instanceof ClangFuncNameToken) {
					PcodeOp op = ((ClangFuncNameToken) token).getPcodeOp();
					if (op == null) {
						continue;  // the function's own name in its signature
					}
					String target = null;
					if (op.getOpcode() == PcodeOp.CALL) {
						Function callee = resolve(currentProgram.getFunctionManager().getFunctionAt(op.getInput(0).getAddress()));
						if (callee != null) {
							target = qname(callee);
						}
					}
					if (target == null) {  // indirect: imports are called through their IAT slot
						target = imported.getOrDefault(token.getText(), token.getText());
					}
					calls.add(new Object[] { lines.size() + 1, target });
				}
			}
			lines.add(sb.toString());
		}
		return new Decompiled(f, lines, calls, false);
	}

	private int writeFile(String rel, List<Function> part, Map<Function, Decompiled> done) throws IOException {
		List<String> text = new ArrayList<>();
		List<Map<String, Object>> entries = new ArrayList<>();
		int failed = 0;
		Namespace ns = part.get(0).getParentNamespace();
		text.add("// " + module + " - " + (ns.isGlobal() ? "global functions" : ns.getName(true)) +
			" (Ghidra " + Application.getApplicationVersion() + ", gst export)");
		List<String> bases = new ArrayList<>();
		List<Object> fields = new ArrayList<>();
		Structure struct = ns instanceof GhidraClass
				? VariableUtilities.findExistingClassStruct((GhidraClass) ns, currentProgram.getDataTypeManager())
				: null;
		if (struct != null && !struct.isNotYetDefined()) {
			text.addAll(layout(ns, struct, bases, fields));
		}
		for (Function f : part) {
			Decompiled d = done.get(f);
			if (d == null) {
				d = new Decompiled(f, List.of("/* not decompiled (cancelled or timed out) */"), List.of(), true);
			}
			if (d.failed) {
				failed++;
			}
			text.add("");
			int start = text.size() + 1;
			text.add("// " + module + "+" + rva(f.getEntryPoint()) + " " + f.getName(true));
			text.addAll(d.lines);
			List<Object> calls = new ArrayList<>();
			for (Object[] c : d.calls) {
				int line = start + (Integer) c[0];
				calls.add(List.of(line, c[1]));
				callRows.add(callRow(f, (String) c[1], rel, line));
			}
			located.put(f, new String[] { rel, Integer.toString(start) });
			entries.add(entry(f, start, text.size(), calls));
		}
		writeText(rel, text);
		Map<String, Object> map = new LinkedHashMap<>();
		map.put("module", module);
		map.put("namespace", ns.isGlobal() ? null : dotted(ns));
		map.put("kind", ns instanceof GhidraClass ? "class" : ns.isGlobal() ? null : "namespace");
		map.put("bases", bases);
		map.put("size", struct == null ? null : struct.getLength());
		map.put("fields", fields);
		map.put("functions", entries);
		Files.writeString(out.resolve(rel + ".map.json"), json(map), StandardCharsets.UTF_8);
		return failed;
	}

	/** Class layout as a comment block (offsets for runtime work), filling bases and fields for the map. */
	private List<String> layout(Namespace ns, Structure struct, List<String> bases, List<Object> fields) {
		List<String> lines = new ArrayList<>();
		DataTypeComponent[] components = struct.getDefinedComponents();
		List<String> shownBases = new ArrayList<>();
		for (DataTypeComponent c : components) {
			String comment = c.getComment() == null ? "" : c.getComment();
			if (c.getFieldName() == null && comment.contains("Base")) {
				bases.add(typeName(c.getDataType(), "."));
				shownBases.add(typeName(c.getDataType(), "::"));
			}
		}
		lines.add("/* class " + ns.getName(true) + (shownBases.isEmpty() ? "" : " : " + String.join(", ", shownBases)) +
			"  (" + struct.getLength() + " bytes; layout from the PDB)");
		for (DataTypeComponent c : components) {
			String comment = c.getComment() == null ? "" : c.getComment();
			boolean base = c.getFieldName() == null && comment.contains("Base");
			String name = base ? "(base)" : c.getFieldName() == null ? c.getDefaultFieldName() : c.getFieldName();
			String type = base ? typeName(c.getDataType(), "::") : c.getDataType().getDisplayName();
			lines.add(String.format("     +0x%-5x %s %s", c.getOffset(), type, name).replace("*/", "* /"));
			fields.add(List.of(c.getOffset(), name, type));
		}
		lines.add("*/");
		return lines;
	}

	/** Name of a class structure: /Game.pdb/sg/Entity -> sg.Entity or sg::Entity (the PDB root category dropped). */
	private static String typeName(DataType dt, String separator) {
		List<String> parts = new ArrayList<>(Arrays.asList(dt.getCategoryPath().getPathElements()));
		if (!parts.isEmpty() && parts.get(0).toLowerCase().endsWith(".pdb")) {
			parts.remove(0);
		}
		parts.add(dt.getName());
		return String.join(separator, parts);
	}

	private void writeListed(String rel, List<Function> part) throws IOException {
		List<String> text = new ArrayList<>();
		List<Map<String, Object>> entries = new ArrayList<>();
		text.add("// " + module + " - functions not decompiled in this export (gst decompile native --functions <regex>)");
		for (Function f : part) {
			int line = text.size() + 1;
			text.add("// " + module + "+" + rva(f.getEntryPoint()) + " " + f.getName(true) + " | " +
				f.getPrototypeString(false, true));
			List<Object> calls = new ArrayList<>();
			for (Function callee : f.getCalledFunctions(monitor)) {
				Function target = resolve(callee);
				if (target != null) {
					calls.add(List.of(line, qname(target)));
					callRows.add(callRow(f, qname(target), rel, line));
				}
			}
			located.put(f, new String[] { rel, Integer.toString(line) });
			entries.add(entry(f, line, line, calls));
		}
		writeText(rel, text);
		Map<String, Object> map = new LinkedHashMap<>();
		map.put("module", module);
		map.put("listed", true);
		map.put("functions", entries);
		Files.writeString(out.resolve(rel + ".map.json"), json(map), StandardCharsets.UTF_8);
	}

	private Map<String, Object> entry(Function f, int line, int end, List<Object> calls) {
		Map<String, Object> m = new LinkedHashMap<>();
		Namespace ns = f.getParentNamespace();
		m.put("q", qname(f));
		m.put("name", f.getName());
		m.put("kind", ns instanceof GhidraClass ? "method" : "function");
		m.put("rva", rva(f.getEntryPoint()));
		m.put("sig", f.getPrototypeString(false, true));
		m.put("line", line);
		m.put("end", end);
		m.put("calls", calls);
		return m;
	}

	private String callRow(Function from, String to, String file, int line) {
		Map<String, Object> m = new LinkedHashMap<>();
		m.put("from", qname(from));
		m.put("from_rva", rva(from.getEntryPoint()));
		m.put("to", to);
		m.put("file", file);
		m.put("line", line);
		return json(m);
	}

	private void writeFunctions(List<Function> functions) throws IOException {
		List<String> rows = new ArrayList<>();
		for (Function f : functions) {
			Map<String, Object> m = new LinkedHashMap<>();
			String[] where = located.get(f);
			m.put("q", qname(f));
			m.put("name", f.getName(true));
			m.put("rva", rva(f.getEntryPoint()));
			m.put("size", f.getBody().getNumAddresses());
			m.put("sig", f.getPrototypeString(false, true));
			m.put("source", f.getSymbol().getSource().toString());
			m.put("file", where == null ? null : where[0]);
			m.put("line", where == null ? null : Integer.valueOf(where[1]));
			rows.add(json(m));
		}
		Files.write(out.resolve("functions.jsonl"), rows, StandardCharsets.UTF_8);
	}

	private int writeImports(FunctionManager fm) throws IOException {
		List<String> rows = new ArrayList<>();
		for (Function f : fm.getExternalFunctions()) {
			ExternalLocation loc = f.getExternalLocation();
			Map<String, Object> m = new LinkedHashMap<>();
			m.put("q", qname(f));
			m.put("lib", loc == null ? null : loc.getLibraryName());
			m.put("name", f.getName());
			rows.add(json(m));
		}
		Files.write(out.resolve("imports.jsonl"), rows, StandardCharsets.UTF_8);
		return rows.size();
	}

	/** Every defined string, streamed row by row (no count cap, full value, every function that references it). */
	private int writeStrings() throws IOException {
		int count = 0;
		ReferenceManager refs = currentProgram.getReferenceManager();
		try (BufferedWriter w = Files.newBufferedWriter(out.resolve("strings.jsonl"), StandardCharsets.UTF_8)) {
			for (Data d : currentProgram.getListing().getDefinedData(true)) {
				if (monitor.isCancelled()) {
					break;
				}
				if (!d.hasStringValue()) {
					continue;
				}
				String value = StringDataInstance.getStringDataInstance(d).getStringValue();
				if (value == null || value.length() < 3) {
					continue;
				}
				Set<String> users = new LinkedHashSet<>();
				for (Reference r : refs.getReferencesTo(d.getAddress())) {
					Function f = getFunctionContaining(r.getFromAddress());
					if (f != null) {
						users.add(qname(f));
					}
				}
				Map<String, Object> m = new LinkedHashMap<>();
				m.put("rva", rva(d.getAddress()));
				m.put("value", value);
				m.put("refs", users);
				w.write(json(m));
				w.newLine();
				count++;
			}
		}
		return count;
	}

	// --- names and paths --------------------------------------------------------------------------

	private Function resolve(Function f) {
		if (f != null && f.isThunk()) {
			Function target = f.getThunkedFunction(true);
			if (target != null) {
				return target;
			}
		}
		return f;
	}

	private String qname(Function f) {
		if (f.isExternal()) {
			ExternalLocation loc = f.getExternalLocation();
			String lib = loc == null || loc.getLibraryName() == null ? "extern" : loc.getLibraryName().toLowerCase();
			int dot = lib.lastIndexOf('.');
			return (dot > 0 ? lib.substring(0, dot) : lib) + "." + f.getName();
		}
		Namespace ns = f.getParentNamespace();
		return ns == null || ns.isGlobal() ? f.getName() : dotted(ns) + "." + f.getName();
	}

	private static String dotted(Namespace ns) {
		Deque<String> parts = new ArrayDeque<>();
		for (Namespace n = ns; n != null && !n.isGlobal(); n = n.getParentNamespace()) {
			parts.addFirst(n.getName());
		}
		return String.join(".", parts);
	}

	private String groupKey(Function f) {
		Namespace ns = f.getParentNamespace();
		if (ns == null || ns.isGlobal()) {
			return "\u0000global";
		}
		Deque<String> parts = new ArrayDeque<>();
		for (Namespace n = ns; n != null && !n.isGlobal(); n = n.getParentNamespace()) {
			parts.addFirst(safe(n.getName()));
		}
		return String.join("/", parts);
	}

	private String filePath(String key, int chunk) {
		String stem = key.equals("\u0000global") ? String.format("_global/%04d", chunk + 1)
				: key + (chunk > 0 ? "." + (chunk + 1) : "");
		String rel = "src/" + stem + ".c";
		for (int n = 2; !usedPaths.add(rel.toLowerCase()); n++) {
			rel = "src/" + stem + "~" + n + ".c";
		}
		return rel;
	}

	/** A file-name part that every shell copes with (PowerShell chokes on ` [ ] ' in paths). */
	private static String safe(String s) {
		String t = s.replaceAll("[^A-Za-z0-9_.~=+-]", "_").replaceAll("_{2,}", "_").replaceAll("[._]+$", "");
		if (t.length() > 60) {
			t = t.substring(0, 50) + "~" + Integer.toHexString(s.hashCode());
		}
		if (t.isEmpty() || t.matches("(?i)(con|prn|aux|nul|com\\d|lpt\\d)(\\..*)?")) {
			t = "_" + t;
		}
		return t;
	}

	private String rva(Address a) {
		return "0x" + Long.toHexString(a.subtract(base));
	}

	private void writeText(String rel, List<String> text) throws IOException {
		Path p = out.resolve(rel);
		Files.createDirectories(p.getParent());
		List<String> safeText = new ArrayList<>(text.size());
		for (String line : text) {
			safeText.add(clean(line));
		}
		Files.write(p, safeText, StandardCharsets.UTF_8);
	}

	// --- minimal JSON ---------------------------------------------------------------------------------

	private static String json(Object o) {
		StringBuilder sb = new StringBuilder();
		write(sb, o);
		return sb.toString();
	}

	@SuppressWarnings("unchecked")
	private static void write(StringBuilder sb, Object o) {
		if (o == null) {
			sb.append("null");
		}
		else if (o instanceof String) {
			quote(sb, clean((String) o));
		}
		else if (o instanceof Number || o instanceof Boolean) {
			sb.append(o);
		}
		else if (o instanceof Map) {
			sb.append('{');
			boolean first = true;
			for (Map.Entry<String, Object> e : ((Map<String, Object>) o).entrySet()) {
				if (!first) {
					sb.append(',');
				}
				first = false;
				quote(sb, e.getKey());
				sb.append(':');
				write(sb, e.getValue());
			}
			sb.append('}');
		}
		else if (o instanceof Collection) {
			sb.append('[');
			boolean first = true;
			for (Object item : (Collection<Object>) o) {
				if (!first) {
					sb.append(',');
				}
				first = false;
				write(sb, item);
			}
			sb.append(']');
		}
		else {
			quote(sb, o.toString());
		}
	}

	private static void quote(StringBuilder sb, String s) {
		sb.append('"');
		for (int i = 0; i < s.length(); i++) {
			char c = s.charAt(i);
			switch (c) {
				case '"': sb.append("\\\""); break;
				case '\\': sb.append("\\\\"); break;
				case '\n': sb.append("\\n"); break;
				case '\r': sb.append("\\r"); break;
				case '\t': sb.append("\\t"); break;
				default:
					if (c < 0x20) {
						sb.append(String.format("\\u%04x", (int) c));
					}
					else {
						sb.append(c);
					}
			}
		}
		sb.append('"');
	}

	/** Lone UTF-16 surrogates (odd string data) become U+FFFD so the UTF-8 writers never fail. */
	private static String clean(String s) {
		StringBuilder sb = null;
		for (int i = 0; i < s.length(); i++) {
			char c = s.charAt(i);
			boolean pair = Character.isHighSurrogate(c) && i + 1 < s.length() && Character.isLowSurrogate(s.charAt(i + 1));
			if (pair) {
				if (sb != null) {
					sb.append(c).append(s.charAt(i + 1));
				}
				i++;
			}
			else if (Character.isSurrogate(c)) {
				if (sb == null) {
					sb = new StringBuilder(s.substring(0, i));
				}
				sb.append('�');
			}
			else if (sb != null) {
				sb.append(c);
			}
		}
		return sb == null ? s : sb.toString();
	}
}
