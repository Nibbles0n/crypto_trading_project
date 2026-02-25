#!/usr/bin/env python3
"""
Pine Script v6 Offline Coding Assistant
Clean, deduplicated version with auto-setup and venv support
"""

import os
import sys
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import re

# Dependency check
def check_dependencies():
    """Check if all required packages are installed"""
    required = {'torch': 'torch', 'transformers': 'transformers', 'rich': 'rich', 
                'huggingface_hub': 'huggingface-hub', 'accelerate': 'accelerate'}
    
    missing = [pkg for module, pkg in required.items() if not __import__(module) or True]
    try:
        for module in required:
            __import__(module)
    except ImportError as e:
        missing = [pkg for module, pkg in required.items()]
        print(f"\n⚠️  Missing packages. Install with: pip install {' '.join(missing)}")
        sys.exit(1)

check_dependencies()

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.syntax import Syntax
from rich.table import Table
from rich.markdown import Markdown
from rich import box
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from huggingface_hub import snapshot_download

console = Console()

# Configuration
DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-32B-Instruct"
APP_VERSION = "1.0.0"


class PineScriptAssistant:
    """Main application class"""
    
    def __init__(self):
        # Setup directories
        self.config_dir = Path.home() / ".pinescript_assistant"
        self.config_dir.mkdir(exist_ok=True)
        
        for subdir in ['models', 'history', 'scripts', 'screenshots']:
            (self.config_dir / subdir).mkdir(exist_ok=True)
        
        self.config_file = self.config_dir / "config.json"
        self.log_file = self.config_dir / "session.log"
        
        # State
        self.current_script: Optional[Path] = None
        self.current_script_content: str = ""
        self.documentation: str = ""
        self.model = None
        self.tokenizer = None
        
        # Detect device
        if torch.cuda.is_available():
            self.device = "cuda"
        elif torch.backends.mps.is_available():
            self.device = "mps"
        else:
            self.device = "cpu"
        
        self.load_config()
    
    def load_config(self):
        """Load or create configuration"""
        if self.config_file.exists():
            with open(self.config_file, 'r') as f:
                self.config = json.load(f)
        else:
            self.config = {
                "model_name": DEFAULT_MODEL,
                "model_path": "",
                "documentation_path": "",
                "max_tokens": 2048,
                "temperature": 0.7,
                "last_script": "",
                "first_run": True
            }
            self.save_config()
    
    def save_config(self):
        """Save configuration"""
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)
    
    def log(self, message: str, level: str = "INFO"):
        """Log to file"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.log_file, 'a') as f:
            f.write(f"[{timestamp}] [{level}] {message}\n")
    
    def download_model(self, model_name: str = DEFAULT_MODEL) -> str:
        """Download model from Hugging Face"""
        try:
            console.print(f"\n[cyan]📥 Downloading {model_name}...[/cyan]")
            console.print(f"[dim]One-time download (~60GB). Cached for future use.[/dim]\n")
            
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                         BarColumn(), DownloadColumn(), TransferSpeedColumn(), 
                         TimeRemainingColumn(), console=console) as progress:
                task = progress.add_task(f"Downloading {model_name}...", total=None)
                
                model_path = snapshot_download(
                    repo_id=model_name,
                    cache_dir=str(self.config_dir / "models"),
                    resume_download=True,
                    local_files_only=False
                )
                
                progress.update(task, completed=True)
            
            console.print(f"[green]✓[/green] Model downloaded to {model_path}\n")
            self.config['model_path'] = model_path
            self.config['model_name'] = model_name
            self.save_config()
            
            return model_path
            
        except Exception as e:
            console.print(f"[red]✗ Download failed: {e}[/red]")
            raise
    
    def setup_model(self, model_path: str = None):
        """Load model with auto-download"""
        try:
            if not model_path:
                if self.config.get('model_path') and Path(self.config['model_path']).exists():
                    model_path = self.config['model_path']
                else:
                    console.print("[yellow]Model not found. Starting download...[/yellow]")
                    model_path = self.download_model()
            
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                         console=console) as progress:
                task = progress.add_task("Loading model...", total=None)
                
                self.log(f"Loading model from {model_path}")
                
                self.tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
                
                # Device-specific loading
                load_kwargs = {
                    "dtype": torch.float16 if self.device != "cpu" else torch.float32,
                    "device_map": {"": self.device} if self.device == "mps" else "auto",
                    "trust_remote_code": True,
                    "low_cpu_mem_usage": True
                }
                
                console.print(f"[dim]Loading with {self.device.upper()} acceleration[/dim]")
                self.model = AutoModelForCausalLM.from_pretrained(model_path, **load_kwargs)
                self.model.eval()
                
                progress.update(task, completed=True)
            
            console.print(f"[green]✓[/green] Model loaded on {self.device.upper()}")
            self.log(f"Model loaded successfully on {self.device}")
            
        except Exception as e:
            console.print(f"[red]✗[/red] Error loading model: {e}")
            self.log(f"Error loading model: {e}", "ERROR")
            raise
    
    def load_documentation(self, doc_path: str = None):
        """Load Pine Script documentation"""
        try:
            if not doc_path:
                if self.config.get('documentation_path'):
                    doc_path = self.config['documentation_path']
                else:
                    console.print("\n[yellow]Pine Script v6 documentation not configured[/yellow]")
                    if Confirm.ask("Load documentation now?", default=True):
                        doc_path = Prompt.ask("Path to Pine Script v6 documentation file")
                    else:
                        console.print("[dim]Add later with 'setup' command[/dim]")
                        return
            
            doc_file = Path(doc_path)
            if not doc_file.exists():
                raise FileNotFoundError(f"Documentation not found: {doc_path}")
            
            with open(doc_file, 'r', encoding='utf-8') as f:
                self.documentation = f.read()
            
            self.config['documentation_path'] = doc_path
            self.save_config()
            
            console.print(f"[green]✓[/green] Documentation loaded ({len(self.documentation)/1024:.1f} KB)")
            
        except Exception as e:
            console.print(f"[red]✗[/red] Error loading documentation: {e}")
    
    def load_script(self, script_path: str):
        """Load Pine Script file"""
        try:
            script_file = Path(script_path)
            if not script_file.exists():
                raise FileNotFoundError(f"Script not found: {script_path}")
            
            with open(script_file, 'r', encoding='utf-8') as f:
                self.current_script_content = f.read()
            
            self.current_script = script_file
            self.config['last_script'] = str(script_file)
            self.save_config()
            self.save_version("Initial load")
            
            console.print(f"[green]✓[/green] Loaded: {script_file.name}")
            
        except Exception as e:
            console.print(f"[red]✗[/red] Error: {e}")
    
    def save_version(self, description: str = ""):
        """Save current version to history"""
        if not self.current_script:
            return
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        version_path = self.config_dir / "history" / f"{self.current_script.stem}_{timestamp}.pine"
        
        with open(version_path, 'w', encoding='utf-8') as f:
            f.write(self.current_script_content)
        
        metadata = {
            "timestamp": timestamp,
            "description": description,
            "original_file": str(self.current_script),
            "version_file": str(version_path)
        }
        
        meta_path = self.config_dir / "history" / f"{self.current_script.stem}_{timestamp}.json"
        with open(meta_path, 'w') as f:
            json.dump(metadata, f, indent=2)
    
    def get_version_history(self) -> List[Dict]:
        """Get all saved versions"""
        versions = []
        for meta_file in sorted((self.config_dir / "history").glob("*.json"), reverse=True):
            with open(meta_file, 'r') as f:
                versions.append(json.load(f))
        return versions
    
    def restore_version(self, version_file: str):
        """Restore a previous version"""
        try:
            with open(version_file, 'r', encoding='utf-8') as f:
                self.current_script_content = f.read()
            
            self.save_version(f"Before restore from {Path(version_file).name}")
            
            if self.current_script:
                with open(self.current_script, 'w', encoding='utf-8') as f:
                    f.write(self.current_script_content)
            
            console.print(f"[green]✓[/green] Version restored")
            
        except Exception as e:
            console.print(f"[red]✗[/red] Error: {e}")
    
    def validate_pine_script(self, code: str) -> Tuple[bool, List[str]]:
        """Validate Pine Script v6 syntax"""
        # Clean markdown artifacts
        code = re.sub(r'```\s*(pinescript)?\s*\n?', '', code.strip())
        code = re.sub(r'```\s*$', '', code.strip())
        
        errors = []
        
        # Version check
        if not re.search(r'//@version\s*=\s*6', code):
            errors.append("Missing //@version=6 directive")
        
        # Required declaration
        if not re.search(r'(indicator|strategy)\s*\(', code):
            errors.append("Missing indicator() or strategy() declaration")
        
        # Bracket matching
        for char, name in [('(', 'parentheses'), ('[', 'brackets'), ('{', 'braces')]:
            closing = ')' if char == '(' else (']' if char == '[' else '}')
            if code.count(char) != code.count(closing):
                errors.append(f"Unmatched {name}")
        
        # Deprecated functions
        deprecated = {'security': 'request.security', 'study': 'indicator', 'time_close': 'time.close'}
        for old, new in deprecated.items():
            if re.search(rf'\b{old}\s*\(', code):
                errors.append(f"Deprecated: {old}() → Use {new}()")
        
        return len(errors) == 0, errors
    
    def generate_edit(self, user_request: str, screenshot_path: Optional[str] = None) -> str:
        """Generate code using LLM"""
        if not self.model:
            raise RuntimeError("Model not loaded")
        
        prompt = self._build_prompt(user_request, screenshot_path)
        
        try:
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                         console=console) as progress:
                task = progress.add_task("Generating...", total=None)
                
                inputs = self.tokenizer(prompt, return_tensors="pt", truncation=True, max_length=8192)
                device = next(self.model.parameters()).device
                inputs = {k: v.to(device) for k, v in inputs.items()}
                
                with torch.no_grad():
                    outputs = self.model.generate(
                        **inputs,
                        max_new_tokens=self.config['max_tokens'],
                        temperature=self.config['temperature'],
                        do_sample=True,
                        top_p=0.95,
                        top_k=50,
                        pad_token_id=self.tokenizer.eos_token_id
                    )
                
                generated = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
                code = self._extract_code(generated, prompt)
                
                progress.update(task, completed=True)
            
            return code
            
        except Exception as e:
            console.print(f"[red]✗[/red] Generation error: {e}")
            raise
    
    def _build_prompt(self, user_request: str, screenshot_path: Optional[str]) -> str:
        """Build LLM prompt"""
        doc_snippet = self.documentation[:5000] if self.documentation else 'No documentation loaded'
        
        prompt = f"""You are an expert Pine Script v6 programmer.

CRITICAL RULES:
- Use request.security() NOT security()
- Use indicator() NOT study()
- Use ta.* prefix for technical functions
- All v6 syntax only

DOCUMENTATION:
{doc_snippet}...

CURRENT CODE:
```pinescript
{self.current_script_content}
```

USER REQUEST:
{user_request}
"""
        
        if screenshot_path:
            prompt += f"\nSCREENSHOT: {screenshot_path}\n"
        
        prompt += """
OUTPUT ONLY complete updated Pine Script v6 code in ```pinescript block.
No explanations. No extra backticks.

```pinescript
"""
        return prompt
    
    def _extract_code(self, generated: str, prompt: str) -> str:
        """Extract code from generation"""
        if prompt in generated:
            generated = generated.split(prompt)[-1]
        
        # Try pinescript block first
        match = re.search(r'```pinescript\s*(.*?)\s*```', generated, re.DOTALL)
        if match:
            return match.group(1).strip()
        
        # Try any code block
        match = re.search(r'```\s*(.*?)\s*```', generated, re.DOTALL)
        if match:
            return match.group(1).strip()
        
        # Clean and return
        code = generated.strip()
        code = re.sub(r'```\s*$', '', code)
        code = re.sub(r'^```pinescript\s*', '', code)
        return code
    
    def apply_edit(self, new_code: str, description: str) -> bool:
        """Apply generated code"""
        is_valid, errors = self.validate_pine_script(new_code)
        
        if not is_valid:
            console.print("\n[yellow]⚠ Validation warnings:[/yellow]")
            for error in errors:
                console.print(f"  • {error}")
            if not Confirm.ask("\nApply anyway?", default=False):
                return False
        
        self.save_version(f"Before: {description}")
        self.current_script_content = new_code
        
        if self.current_script:
            with open(self.current_script, 'w', encoding='utf-8') as f:
                f.write(self.current_script_content)
        
        self.save_version(f"After: {description}")
        console.print(f"[green]✓[/green] Applied")
        return True
    
    def show_current_script(self):
        """Display current script"""
        if not self.current_script_content:
            console.print("[yellow]No script loaded[/yellow]")
            return
        
        syntax = Syntax(self.current_script_content, "javascript", theme="monokai", 
                       line_numbers=True, word_wrap=True)
        title = self.current_script.name if self.current_script else 'Current Script'
        console.print(Panel(syntax, title=f"[bold cyan]{title}[/bold cyan]", border_style="cyan"))
    
    def show_history(self):
        """Display version history"""
        versions = self.get_version_history()
        if not versions:
            console.print("[yellow]No history[/yellow]")
            return
        
        table = Table(title="Version History", box=box.ROUNDED)
        table.add_column("Index", style="cyan", width=6)
        table.add_column("Timestamp", style="magenta", width=20)
        table.add_column("Description", style="white")
        
        for i, v in enumerate(versions):
            ts = datetime.strptime(v['timestamp'], "%Y%m%d_%H%M%S")
            table.add_row(str(i), ts.strftime("%Y-%m-%d %H:%M:%S"), v['description'])
        
        console.print(table)
    
    def export_script(self):
        """Export current script"""
        if not self.current_script_content:
            console.print("[yellow]No script[/yellow]")
            return
        
        default = f"{self.current_script.stem}_exported.pine" if self.current_script else "exported.pine"
        path = Prompt.ask("Export filename", default=default)
        
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self.current_script_content)
            console.print(f"[green]✓[/green] Exported to {path}")
        except Exception as e:
            console.print(f"[red]✗[/red] Error: {e}")
    
    def add_file_to_folder(self, file_path: str, folder_type: str):
        """Add file to scripts or screenshots folder"""
        try:
            src = Path(file_path)
            if not src.exists():
                raise FileNotFoundError(f"File not found: {file_path}")
            
            dest_dir = self.config_dir / folder_type
            dest = dest_dir / src.name
            
            if dest.exists() and not Confirm.ask(f"{dest.name} exists. Overwrite?"):
                return False
            
            shutil.copy2(src, dest)
            console.print(f"[green]✓[/green] Added to {folder_type}/")
            return True
        except Exception as e:
            console.print(f"[red]✗[/red] Error: {e}")
            return False
    
    def browse_folder(self, folder_type: str, extensions: List[str]) -> Optional[Path]:
        """Browse and select file from folder"""
        folder = self.config_dir / folder_type
        files = []
        for ext in extensions:
            files.extend(folder.glob(f"*.{ext}"))
        
        if not files:
            console.print(f"[yellow]No {folder_type} files[/yellow]")
            return None
        
        table = Table(title=f"{folder_type.title()} Files", box=box.ROUNDED)
        table.add_column("Index", style="cyan", width=6)
        table.add_column("Filename", style="white")
        table.add_column("Size", style="green", width=10)
        
        for i, f in enumerate(files):
            table.add_row(str(i), f.name, f"{f.stat().st_size / 1024:.1f} KB")
        
        console.print(table)
        
        while True:
            choice = Prompt.ask(f"Select index (0-{len(files)-1}) or 'cancel'")
            if choice.lower() == 'cancel':
                return None
            try:
                idx = int(choice)
                if 0 <= idx < len(files):
                    return files[idx]
                console.print(f"[red]Invalid index[/red]")
            except ValueError:
                console.print("[red]Enter a number or 'cancel'[/red]")


def display_header():
    """Display app header"""
    console.clear()
    console.print("""
╔═══════════════════════════════════════════════════════════════╗
║     🌲 Pine Script v6 Offline Coding Assistant 🌲             ║
╚═══════════════════════════════════════════════════════════════╝
""", style="bold green")


def display_menu():
    """Display command menu"""
    commands = [
        ("edit", "Make AI-powered changes"),
        ("view", "View current script"),
        ("validate", "Check for errors"),
        ("history", "View version history"),
        ("restore", "Restore previous version"),
        ("export", "Export script"),
        ("load", "Load script"),
        ("scripts", "Browse scripts folder"),
        ("screenshots", "Browse screenshots"),
        ("help", "Show help"),
        ("quit", "Exit")
    ]
    
    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    table.add_column("Command", style="cyan bold", width=20)
    table.add_column("Description", style="white")
    
    for cmd, desc in commands:
        table.add_row(cmd, desc)
    
    console.print("\n")
    console.print(Panel(table, title="[bold]Commands[/bold]", border_style="blue"))


def main():
    """Main application loop"""
    assistant = PineScriptAssistant()
    display_header()
    
    # First run
    if assistant.config.get('first_run', True):
        console.print("\n[bold cyan]🎉 Welcome![/bold cyan]\n")
        console.print("Setting up automatically...\n")
        assistant.config['first_run'] = False
        assistant.save_config()
    
    # Setup
    console.print(f"[dim]Loading model on {assistant.device.upper()}...[/dim]")
    assistant.setup_model()
    assistant.load_documentation()
    
    # Load last script
    if assistant.config['last_script'] and Path(assistant.config['last_script']).exists():
        if Confirm.ask("Load last script?", default=True):
            assistant.load_script(assistant.config['last_script'])
    
    # Main loop
    while True:
        try:
            display_menu()
            console.print()
            command = Prompt.ask("[bold cyan]Command[/bold cyan]", default="help").lower().strip()
            console.print()
            
            if command in ["quit", "exit", "q"]:
                if Confirm.ask("Exit?", default=True):
                    console.print("\n[green]Goodbye! 🌲[/green]\n")
                    break
            
            elif command in ["edit", "e"]:
                if not assistant.current_script:
                    console.print("[yellow]Load a script first[/yellow]")
                    continue
                
                request = Prompt.ask("[bold]Describe changes[/bold]")
                screenshot = None
                
                if Confirm.ask("Include screenshot?", default=False):
                    screenshot_file = assistant.browse_folder("screenshots", ["png", "jpg", "jpeg"])
                    screenshot = str(screenshot_file) if screenshot_file else None
                
                code = assistant.generate_edit(request, screenshot)
                
                console.print("\n[bold cyan]Generated:[/bold cyan]")
                syntax = Syntax(code, "javascript", theme="monokai", line_numbers=True)
                console.print(Panel(syntax, border_style="green"))
                
                if Confirm.ask("\nApply?", default=True):
                    assistant.apply_edit(code, request)
            
            elif command in ["view", "v"]:
                assistant.show_current_script()
            
            elif command == "validate":
                if assistant.current_script_content:
                    valid, errors = assistant.validate_pine_script(assistant.current_script_content)
                    if valid:
                        console.print("[green]✓ Valid[/green]")
                    else:
                        console.print("[yellow]⚠ Issues:[/yellow]")
                        for e in errors:
                            console.print(f"  • {e}")
                else:
                    console.print("[yellow]No script[/yellow]")
            
            elif command in ["history", "h"]:
                assistant.show_history()
            
            elif command in ["restore", "r"]:
                assistant.show_history()
                versions = assistant.get_version_history()
                if versions:
                    idx = Prompt.ask("Version index", default="0")
                    try:
                        assistant.restore_version(versions[int(idx)]['version_file'])
                    except (ValueError, IndexError):
                        console.print("[red]Invalid index[/red]")
            
            elif command == "export":
                assistant.export_script()
            
            elif command in ["load", "l"]:
                path = Prompt.ask("Script path")
                assistant.load_script(path)
            
            elif command == "scripts":
                script = assistant.browse_folder("scripts", ["pine"])
                if script:
                    assistant.load_script(str(script))
            
            elif command == "screenshots":
                assistant.browse_folder("screenshots", ["png", "jpg", "jpeg"])
            
            elif command == "help":
                help_md = """# Pine Script Assistant

## Quick Start
1. **load** - Load a Pine Script file
2. **edit** - AI generates changes
3. **view** - See current code
4. **Apply** - Save with version control

## Features
- Offline AI coding
- Auto syntax validation
- Complete version history
- Organized file management

Press Enter..."""
                console.print(Markdown(help_md))
                input()
            
            else:
                console.print(f"[red]Unknown command[/red]")
            
            console.print("\n" + "─" * 60 + "\n")
            
        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled[/yellow]")
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")
            assistant.log(f"Error: {e}", "ERROR")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        console.print(f"\n[red]Fatal: {e}[/red]")
        sys.exit(1)