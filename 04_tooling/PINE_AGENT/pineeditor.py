#!/usr/bin/env python3
"""
Pine Script AI Editor - Interactive CLI
Easy-to-use interface for the multi-phase Pine Script editor
"""

import sys
import os
from pathlib import Path
import json
from typing import Optional


class PineEditorCLI:
    """Interactive command-line interface for Pine Script editing"""
    
    def __init__(self):
        self.config_file = Path.home() / ".pine_editor_config.json"
        self.config = self.load_config()
        self.editor = None
    
    def load_config(self) -> dict:
        """Load configuration from file"""
        if self.config_file.exists():
            return json.loads(self.config_file.read_text())
        
        # Default configuration
        return {
            "planner_model": "Qwen/Qwen2.5-14B-Instruct",
            "coder_model": "Qwen/Qwen2.5-Coder-7B-Instruct",
            "checker_model": "Qwen/Qwen2.5-1.5B-Instruct",
            "doc_db": "pine_docs.db",
            "script_file": "pine.txt"
        }
    
    def save_config(self):
        """Save configuration to file"""
        self.config_file.write_text(json.dumps(self.config, indent=2))
        print(f"✓ Configuration saved to {self.config_file}")
    
    def setup_wizard(self):
        """Interactive setup wizard"""
        print("\n" + "="*60)
        print("Pine Script AI Editor - Setup Wizard")
        print("="*60)
        
        print("\nThis wizard will help you configure the editor.")
        print("Press Enter to keep the current value.\n")
        
        # Model paths
        print("Model Configuration")
        print("-" * 40)
        
        planner = input(f"Planning model (14B) [{self.config['planner_model']}]: ").strip()
        if planner:
            self.config['planner_model'] = planner
        
        coder = input(f"Coding model (7B) [{self.config['coder_model']}]: ").strip()
        if coder:
            self.config['coder_model'] = coder
        
        checker = input(f"Syntax checker model (1.5-3B) [{self.config['checker_model']}]: ").strip()
        if checker:
            self.config['checker_model'] = checker
        
        # File paths
        print("\nFile Paths")
        print("-" * 40)
        
        db = input(f"Documentation database [{self.config['doc_db']}]: ").strip()
        if db:
            self.config['doc_db'] = db
        
        script = input(f"Pine Script file [{self.config['script_file']}]: ").strip()
        if script:
            self.config['script_file'] = script
        
        self.save_config()
        
        print("\n✓ Setup complete!")
        print("\nNext steps:")
        print("  1. Ensure your models are downloaded")
        print("  2. Create your documentation database: python pine_db_setup.py --create-sample")
        print("  3. Start editing: python pine_editor_cli.py edit")
    
    def verify_setup(self) -> bool:
        """Verify that all required files exist"""
        errors = []
        
        # Check documentation database
        if not Path(self.config['doc_db']).exists():
            errors.append(f"Documentation database not found: {self.config['doc_db']}")
            errors.append("  Run: python pine_db_setup.py --create-sample")
        
        # Check script file
        if not Path(self.config['script_file']).exists():
            print(f"⚠ Script file not found: {self.config['script_file']}")
            create = input("Create empty script file? [Y/n]: ").strip().lower()
            if create != 'n':
                Path(self.config['script_file']).write_text(
                    "//@version=5\nindicator('My Script')\nplot(close)\n"
                )
                print(f"✓ Created {self.config['script_file']}")
        
        if errors:
            print("\n❌ Setup incomplete:")
            for error in errors:
                print(f"  {error}")
            print("\nRun 'setup' command to configure the editor.")
            return False
        
        return True
    
    def init_editor(self):
        """Initialize the editor (lazy loading)"""
        if self.editor is None:
            # Import here to avoid loading everything until needed
            from pine_script_editor import PineScriptEditor
            
            self.editor = PineScriptEditor(
                planner_model=self.config['planner_model'],
                coder_model=self.config['coder_model'],
                checker_model=self.config['checker_model'],
                doc_db_path=self.config['doc_db'],
                script_file=self.config['script_file']
            )
    
    def interactive_edit(self):
        """Interactive editing session"""
        if not self.verify_setup():
            return
        
        print("\n" + "="*60)
        print("Pine Script AI Editor - Interactive Mode")
        print("="*60)
        print("\nCommands:")
        print("  edit <request>  - Make a change to the script")
        print("  show           - Show current script")
        print("  plan <request> - Create a plan without executing")
        print("  reload         - Reload the script from disk")
        print("  config         - Show configuration")
        print("  quit           - Exit")
        print()
        
        while True:
            try:
                command = input("\npine> ").strip()
                
                if not command:
                    continue
                
                if command == "quit" or command == "exit":
                    break
                
                elif command == "show":
                    self.show_script()
                
                elif command == "config":
                    self.show_config()
                
                elif command == "reload":
                    self.reload_script()
                
                elif command.startswith("edit "):
                    request = command[5:].strip()
                    self.execute_edit(request, skip_planning=False)
                
                elif command.startswith("plan "):
                    request = command[5:].strip()
                    self.execute_plan_only(request)
                
                else:
                    print(f"Unknown command: {command}")
                    print("Type 'quit' to exit or see commands above.")
            
            except KeyboardInterrupt:
                print("\n\nUse 'quit' to exit.")
            except Exception as e:
                print(f"\n❌ Error: {e}")
                import traceback
                traceback.print_exc()
    
    def show_script(self):
        """Display the current script"""
        script_path = Path(self.config['script_file'])
        if not script_path.exists():
            print("Script file does not exist yet.")
            return
        
        script = script_path.read_text()
        print("\n" + "="*60)
        print(f"Current Script: {script_path}")
        print("="*60)
        print(script)
        print("="*60)
        print(f"Length: {len(script)} chars, {len(script.split(chr(10)))} lines")
    
    def show_config(self):
        """Display current configuration"""
        print("\n" + "="*60)
        print("Configuration")
        print("="*60)
        for key, value in self.config.items():
            print(f"{key:20s}: {value}")
        print("="*60)
        print(f"Config file: {self.config_file}")
    
    def reload_script(self):
        """Reload script from disk"""
        if self.editor:
            script = self.editor.load_script()
            print(f"✓ Reloaded script ({len(script)} chars)")
        else:
            self.show_script()
    
    def execute_edit(self, request: str, skip_planning: bool = False):
        """Execute a full edit operation"""
        self.init_editor()
        
        print(f"\n{'='*60}")
        print(f"Request: {request}")
        print('='*60)
        
        try:
            results = self.editor.edit(request, skip_planning=skip_planning)
            
            # Show results
            print("\n" + "="*60)
            print("RESULTS")
            print("="*60)
            
            if results.get('plan'):
                print("\nPlan:")
                plan = results['plan']
                if isinstance(plan, dict):
                    for key, value in plan.items():
                        if key != 'raw_response':
                            print(f"  {key}: {str(value)[:200]}")
            
            print(f"\nModified: {len(results['modified_code'])} characters")
            
            if results['syntax_errors']:
                print(f"\n⚠ Found {len(results['syntax_errors'])} syntax issues:")
                for err in results['syntax_errors']:
                    print(f"  Line {err.get('line', '?')}: {err.get('error', 'Unknown')}")
            else:
                print("\n✓ No syntax errors")
            
            if results.get('saved'):
                print(f"\n✓ Saved to {self.config['script_file']}")
            else:
                print("\n⚠ Not saved due to errors")
            
            # Ask to show the code
            show = input("\nShow modified code? [Y/n]: ").strip().lower()
            if show != 'n':
                print("\n" + "="*60)
                print("Modified Code")
                print("="*60)
                print(results['modified_code'])
                print("="*60)
        
        except Exception as e:
            print(f"\n❌ Error during edit: {e}")
            import traceback
            traceback.print_exc()
    
    def execute_plan_only(self, request: str):
        """Execute planning phase only"""
        self.init_editor()
        
        print(f"\n{'='*60}")
        print(f"Planning for: {request}")
        print('='*60)
        
        try:
            # Load script
            from pine_script_editor import PineScriptContext
            script = self.editor.load_script()
            context = PineScriptContext(
                original_script=script,
                current_script=script,
                user_request=request
            )
            
            # Create plan
            self.editor._ensure_phase(1)
            plan = self.editor.planner.create_plan(context)
            
            # Display plan
            print("\n" + "="*60)
            print("PLAN")
            print("="*60)
            
            if isinstance(plan, dict):
                for key, value in plan.items():
                    print(f"\n{key.upper()}:")
                    if isinstance(value, list):
                        for item in value:
                            print(f"  - {item}")
                    else:
                        print(f"  {value}")
            else:
                print(plan)
            
            print("\n" + "="*60)
            
            # Ask if they want to proceed
            proceed = input("\nProceed with implementation? [y/N]: ").strip().lower()
            if proceed == 'y':
                self.execute_edit(request, skip_planning=True)
        
        except Exception as e:
            print(f"\n❌ Error during planning: {e}")
            import traceback
            traceback.print_exc()
    
    def quick_edit(self, request: str):
        """Quick edit from command line"""
        if not self.verify_setup():
            return
        
        self.init_editor()
        
        try:
            results = self.editor.edit(request, skip_planning=False)
            
            if results.get('saved'):
                print(f"✓ Successfully modified {self.config['script_file']}")
            else:
                print(f"⚠ Modifications completed but not saved due to errors")
                
        finally:
            if self.editor:
                self.editor.cleanup()


def main():
    """Main CLI entry point"""
    cli = PineEditorCLI()
    
    if len(sys.argv) < 2:
        print("Pine Script AI Editor")
        print("\nUsage:")
        print("  pine_editor_cli.py setup              - Run setup wizard")
        print("  pine_editor_cli.py interactive         - Start interactive mode")
        print("  pine_editor_cli.py edit <request>      - Quick edit")
        print("  pine_editor_cli.py show                - Show current script")
        print()
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "setup":
        cli.setup_wizard()
    
    elif command == "interactive" or command == "i":
        cli.interactive_edit()
    
    elif command == "edit" or command == "e":
        if len(sys.argv) < 3:
            print("Usage: pine_editor_cli.py edit <request>")
            sys.exit(1)
        request = " ".join(sys.argv[2:])
        cli.quick_edit(request)
    
    elif command == "show":
        cli.show_script()
    
    elif command == "config":
        cli.show_config()
    
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()