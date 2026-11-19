// Game Study Toolkit: IL2CPP names from decomp/il2cpp/names.jsonl (gst.engines.il2cpp.names) applied to GameAssembly.
// Args: <names.jsonl> <pre|post> <report.json or -> [ns|flat]
//   pre  (prescript, before auto-analysis): a label at image base + RVA for every method, in a namespace per type
//        (C# namespace parts, then the type as a class), each address marked as an entry point so the analysis
//        disassembles and creates the function there with that name and correct boundaries; labels and comments
//        for MethodInfo/TypeInfo/metadata slots and string literals (the literal text as a comment).
//   post (postscript, before GsExport): every method address gets a function (disassembled and created when the
//        analysis did not make one), named and moved into its namespace when it is not already, with a plate
//        comment holding the C# signature and the other methods folded onto the same code. Writes the report
//        (counts of what the analysis had already done versus what post had to fix). No limit on the count.
//   ns (default) puts functions in namespaces; flat names them Namespace.Type$$Method in the global namespace.
// @category GameStudy

import java.io.BufferedReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

import com.google.gson.*;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.symbol.*;

public class GstIl2cppNames extends GhidraScript {

	private static final int ALSO_SHOWN = 20;  // plate comment only; names.jsonl keeps every folded name

	private final Map<String, Namespace> namespaces = new HashMap<>();
	private final Map<String, Long> counts = new TreeMap<>();
	private final List<String> errors = new ArrayList<>();  // every error, in the report
	private Address base;
	private Memory memory;
	private SymbolTable symbols;
	private boolean flat;

	@Override
	protected void run() throws Exception {
		String[] args = getScriptArgs();
		Path namesPath = Paths.get(args[0]);
		String mode = args.length > 1 ? args[1] : "post";
		String report = args.length > 2 ? args[2] : "-";
		flat = args.length > 3 && "flat".equals(args[3]);
		base = currentProgram.getImageBase();
		memory = currentProgram.getMemory();
		symbols = currentProgram.getSymbolTable();
		long t0 = System.currentTimeMillis();
		JsonObject header = null;
		try (BufferedReader in = Files.newBufferedReader(namesPath, StandardCharsets.UTF_8)) {
			String line;
			long n = 0;
			while ((line = in.readLine()) != null) {
				if (line.isBlank()) {
					continue;
				}
				JsonObject e = JsonParser.parseString(line).getAsJsonObject();
				if (e.has("_header")) {
					header = e;
					continue;
				}
				if (monitor.isCancelled()) {
					break;
				}
				try {
					apply(e, mode);
				}
				catch (Exception ex) {  // one bad name must not stop the others (or the export after this script)
					count("errors", 1);
					errors.add(str(e, "name") + " @ rva " + e.get("rva") + ": " + ex);
				}
				if (++n % 50000 == 0) {
					println("gst: il2cpp names " + mode + ": " + n + " entries");
				}
			}
		}
		count("seconds_" + mode, (System.currentTimeMillis() - t0) / 1000);
		if ("post".equals(mode)) {
			FunctionManager fm = currentProgram.getFunctionManager();
			long named = 0;
			long total = 0;
			for (Function f : fm.getFunctions(true)) {
				total++;
				if (f.getSymbol().getSource() != SourceType.DEFAULT) {
					named++;
				}
			}
			count("functions_total", total);
			count("functions_named", named);
		}
		if (!"-".equals(report)) {
			Map<String, Object> out = new LinkedHashMap<>();
			out.put("mode", mode);
			out.put("names", namesPath.toString());
			out.put("image_base", "0x" + base.toString());
			out.put("namespaces", flat ? "flat" : "ns");
			out.put("names_header_counts", header == null ? null : header.get("counts"));  // JsonElement: ints stay ints
			out.put("counts", counts);
			out.put("first_errors", errors);
			Files.writeString(Paths.get(report), new GsonBuilder().setPrettyPrinting().serializeNulls().create().toJson(out) + "\n",
				StandardCharsets.UTF_8);
		}
		println("gst: il2cpp names " + mode + " done: " + counts);
	}

	private void apply(JsonObject e, String mode) throws Exception {
		String kind = str(e, "kind");
		long rva = e.get("rva").getAsLong();
		Address addr;
		try {
			addr = base.add(rva);
		}
		catch (Exception ex) {
			count("out_of_image", 1);
			return;
		}
		if (!memory.contains(addr)) {
			count("out_of_image", 1);
			return;
		}
		if ("method".equals(kind) || "function".equals(kind)) {
			if ("pre".equals(mode)) {
				preMethod(e, kind, addr);
			}
			else {
				postMethod(e, kind, addr);
			}
		}
		else {
			data(e, kind, addr);
		}
	}

	// --- methods ------------------------------------------------------------------------------------

	private void preMethod(JsonObject e, String kind, Address addr) throws Exception {
		symbols.addExternalEntryPoint(addr);
		count("entry_points", 1);
		if ("function".equals(kind) || str(e, "name").isEmpty()) {
			return;
		}
		Namespace ns = namespaceOf(e);
		String name = functionName(e);
		Symbol existing = find(addr, name, ns);
		Symbol label = existing != null ? existing : symbols.createLabel(addr, name, ns, SourceType.IMPORTED);
		if (!label.isPrimary()) {
			label.setPrimary();
		}
		count("labels", 1);
	}

	private void postMethod(JsonObject e, String kind, Address addr) throws Exception {
		FunctionManager fm = currentProgram.getFunctionManager();
		Function f = fm.getFunctionAt(addr);
		if (f == null) {
			Function containing = fm.getFunctionContaining(addr);
			if (getInstructionAt(addr) == null) {
				disassemble(addr);
			}
			f = createFunction(addr, null);
			if (f == null) {
				count("failed_" + kind, 1);
				return;
			}
			count(containing != null ? "created_inside_other_function" : "created_" + kind, 1);
		}
		else {
			count("existing_" + kind, 1);
		}
		if ("function".equals(kind) || str(e, "name").isEmpty()) {
			return;
		}
		Namespace ns = namespaceOf(e);
		String name = functionName(e);
		if (f.getName().equals(name) && f.getParentNamespace().equals(ns)) {
			count("already_named", 1);
		}
		else {
			count(f.getSymbol().getSource() == SourceType.DEFAULT ? "renamed_default" : "renamed_other", 1);
			Symbol label = find(addr, name, ns);  // the pre label the function did not adopt (an export name won)
			if (label != null && !label.equals(f.getSymbol())) {
				label.delete();
			}
			if (!f.getParentNamespace().equals(ns)) {
				f.setParentNamespace(ns);
			}
			f.setName(name, SourceType.IMPORTED);
		}
		setPlateComment(addr, plate(e));
	}

	private String plate(JsonObject e) {
		StringBuilder sb = new StringBuilder();
		String signature = str(e, "signature");
		if (!signature.isEmpty()) {
			sb.append(signature).append('\n');
		}
		sb.append(str(e, "name")).append("  [").append(str(e, "source")).append(']');
		JsonArray also = e.has("also") && e.get("also").isJsonArray() ? e.getAsJsonArray("also") : null;
		if (also != null && also.size() > 0) {
			sb.append("\nsame code as ").append(also.size()).append(" other method(s)");
			for (int i = 0; i < also.size() && i < ALSO_SHOWN; i++) {
				sb.append("\n  ").append(also.get(i).getAsString());
			}
			if (also.size() > ALSO_SHOWN) {
				sb.append("\n  ... (every name: decomp/il2cpp/names.jsonl)");
			}
		}
		return sb.toString();
	}

	// --- data: MethodInfo, TypeInfo, metadata slots, string literals ------------------------------------

	private void data(JsonObject e, String kind, Address addr) throws Exception {
		String name = str(e, "name");
		if (name.isEmpty()) {
			return;
		}
		Namespace global = currentProgram.getGlobalNamespace();
		Symbol existing = find(addr, name, global);
		if (existing == null) {
			Symbol label = symbols.createLabel(addr, name, global, SourceType.IMPORTED);
			if (!label.isPrimary()) {
				label.setPrimary();
			}
			count("labels_" + kind, 1);
		}
		String comment = "string_literal".equals(kind) ? str(e, "value") : str(e, "signature");
		if (!comment.isEmpty()) {
			setEOLComment(addr, comment.replace("\r", "\\r").replace("\n", "\\n"));
		}
	}

	// --- names and namespaces -------------------------------------------------------------------------

	private String functionName(JsonObject e) {
		if (flat || !e.has("ns") || !e.has("fn")) {
			return str(e, "name");
		}
		return str(e, "fn");
	}

	private Namespace namespaceOf(JsonObject e) throws Exception {
		Namespace global = currentProgram.getGlobalNamespace();
		if (flat || !e.has("ns") || !e.get("ns").isJsonArray()) {
			return global;
		}
		JsonArray parts = e.getAsJsonArray("ns");
		Namespace parent = global;
		StringBuilder key = new StringBuilder();
		for (int i = 0; i < parts.size(); i++) {
			String part = parts.get(i).getAsString();
			key.append('\u0001').append(part);
			Namespace cached = namespaces.get(key.toString());
			if (cached == null) {
				cached = symbols.getNamespace(part, parent);
				if (cached == null) {
					boolean type = i == parts.size() - 1;
					try {
						cached = type ? symbols.createClass(parent, part, SourceType.IMPORTED)
								: symbols.createNameSpace(parent, part, SourceType.IMPORTED);
						count(type ? "classes" : "namespaces", 1);
					}
					catch (Exception ex) {  // a name Ghidra refuses: keep the function in the parent
						count("namespace_errors", 1);
						cached = parent;
					}
				}
				namespaces.put(key.toString(), cached);
			}
			parent = cached;
		}
		return parent;
	}

	private Symbol find(Address addr, String name, Namespace ns) {
		for (Symbol s : symbols.getSymbols(addr)) {
			if (s.getName().equals(name) && s.getParentNamespace().equals(ns)) {
				return s;
			}
		}
		return null;
	}

	private static String str(JsonObject e, String key) {
		JsonElement v = e.get(key);
		return v == null || v.isJsonNull() ? "" : v.getAsString();
	}

	private void count(String key, long n) {
		counts.merge(key, n, Long::sum);
	}
}
