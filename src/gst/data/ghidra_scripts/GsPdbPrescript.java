// Game Study Toolkit prescript: debug symbols only from a local file.
// Arg: path of the .pdb shipped next to the module, or "-" when there is none. With no local PDB the PDB
// analyzer is switched off, so analysis never asks a symbol server (PDB names and GUIDs stay on this machine).
// With a PDB, classes are laid out with their base classes and vtable pointer ("Class Hierarchy"), so the
// decompiler shows this->hp instead of *(int *)(&this->_padding_ + 4). That choice is visible only when
// Ghidra runs with -Dghidra.pdb.developerMode=true (gst sets it).
// @category GameStudy

import java.io.File;

import ghidra.app.plugin.core.analysis.PdbUniversalAnalyzer;
import ghidra.app.script.GhidraScript;
import ghidra.app.util.pdb.pdbapplicator.ObjectOrientedClassLayout;
import ghidra.framework.options.Options;
import ghidra.program.model.listing.Program;

public class GsPdbPrescript extends GhidraScript {

	@Override
	protected void run() throws Exception {
		String[] args = getScriptArgs();
		String pdb = args.length > 0 ? args[0] : "-";
		if (!"-".equals(pdb) && new File(pdb).isFile()) {
			PdbUniversalAnalyzer.setPdbFileOption(currentProgram, new File(pdb));
			PdbUniversalAnalyzer.setAllowUntrustedOption(currentProgram, false);
			Options options = currentProgram.getOptions(Program.ANALYSIS_PROPERTIES).getOptions("PDB Universal");
			if (options.getOptionNames().contains("Composite Layout Choice")) {
				options.setEnum("Composite Layout Choice", ObjectOrientedClassLayout.CLASS_HIERARCHY);
			}
			println("gst: PDB " + pdb + "; class layout " +
				options.getEnum("Composite Layout Choice", ObjectOrientedClassLayout.MEMBERS_ONLY));
		}
		else {
			setAnalysisOption(currentProgram, "PDB Universal", "false");
			println("gst: no local PDB; PDB analysis off");
		}
	}
}
