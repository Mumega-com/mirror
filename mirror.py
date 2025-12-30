
import argparse
import subprocess
import sys
import os
from typing import List

# Project Mirror Unified CLI
# Version: 1.0 (Crystallization)

WORKSPACE_ROOT = "/Users/hadi/Development/Mirror"

def run_command(command: List[str], description: str):
    """Execute a subprocess command with visual feedback."""
    print(f"\n🔮 [Mirror] Initiating: {description}...")
    print(f"   Command: {' '.join(command)}\n")
    try:
        # Pass through the current environment
        env = os.environ.copy()
        subprocess.run(command, cwd=WORKSPACE_ROOT, env=env, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n🛑 [Mirror] Execution Failed: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n🛑 [Mirror] Interrupted by User.")
        sys.exit(0)

def handle_swarm(args):
    """Dispatch to mirror_swarm.py"""
    cmd = ["python3", "mirror_swarm.py", args.task]
    if args.foci:
        cmd.extend(["--foci"] + args.foci)
    run_command(cmd, "Universal Swarm")

def handle_pulse(args):
    """Dispatch to mirror_pulse.py"""
    # Assuming mirror_pulse.py can be run to just show status or start a daemon
    # Currently it seems designed as a library or import, let's allow running it if it has a main
    # If not, we might need to create a wrapper or just check its file.
    cmd = ["python3", "mirror_pulse.py"] 
    run_command(cmd, "16D Pulse Check")

def handle_evolve(args):
    """Dispatch to mirror_evolution.py"""
    cmd = ["python3", "mirror_evolution.py", args.target]
    if args.apply:
        cmd.append("--apply")
    run_command(cmd, "Recursive Self-Grafting")

def handle_probe(args):
    """Dispatch to mirror_probe_pdf.py (or similar extraction tool)"""
    cmd = ["python3", "mirror_probe_pdf.py"]
    if args.path:
        cmd.extend(["--path", args.path])
    run_command(cmd, "Knowledge Ingestion")

def main():
    parser = argparse.ArgumentParser(
        description="Project Mirror: Unified Command Interface",
        formatter_class=argparse.RawTextHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="Available Modes")

    # Swarm Command
    swarm_parser = subparsers.add_parser("swarm", help="Deploy the Multi-Agent Swarm")
    swarm_parser.add_argument("task", help="The objective for the swarm")
    swarm_parser.add_argument("--foci", nargs="+", help="Specific focus areas (e.g. 'Security' 'Speed')")

    # Pulse Command
    pulse_parser = subparsers.add_parser("pulse", help="Check 16D Witness Status")
    
    # Evolve Command
    evolve_parser = subparsers.add_parser("evolve", help="Run Evolution Engine on a file")
    evolve_parser.add_argument("target", help="The file to analyze/patch")
    evolve_parser.add_argument("--apply", action="store_true", help="Auto-apply the patch if valid (DANGEROUS)")

    # Probe Command
    probe_parser = subparsers.add_parser("probe", help="Ingest documents into Engram Library")
    probe_parser.add_argument("--path", help="Path to PDF/Text to ingest")

    args = parser.parse_args()

    if args.command == "swarm":
        handle_swarm(args)
    elif args.command == "pulse":
        handle_pulse(args)
    elif args.command == "evolve":
        handle_evolve(args)
    elif args.command == "probe":
        handle_probe(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
