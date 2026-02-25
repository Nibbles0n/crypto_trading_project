#!/usr/bin/env python3
"""
BUILD_MACOS_APP.PY - macOS Application Builder
Run this script to create PineScriptAssistant.app

Usage:
    python build_macos_app.py

This will create a distributable .app bundle that includes:
- The Pine Script assistant
- Auto-download for Qwen2.5-Coder-32B-Instruct
- All dependencies bundled
- Double-clickable launcher
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path
import textwrap

# Configuration
APP_NAME = "PineScriptAssistant"
APP_DISPLAY_NAME = "Pine Script Assistant"
BUNDLE_ID = "com.local.pinescriptassistant"
VERSION = "1.0.0"
DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-32B-Instruct"

def create_app_bundle():
    """Create the macOS .app bundle structure"""
    print("\n🔨 Building macOS Application Bundle...")
    print("=" * 60)
    
    # Paths
    app_path = Path(f"{APP_NAME}.app")
    contents_dir = app_path / "Contents"
    macos_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"
    
    # Clean existing
    if app_path.exists():
        print(f"🗑️  Removing existing {APP_NAME}.app")
        shutil.rmtree(app_path)
    
    # Create structure
    print(f"📁 Creating bundle structure...")
    macos_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)
    
    # Create Info.plist
    print(f"📝 Creating Info.plist...")
    info_plist = textwrap.dedent(f'''<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
        <key>CFBundleName</key>
        <string>{APP_DISPLAY_NAME}</string>
        <key>CFBundleDisplayName</key>
        <string>{APP_DISPLAY_NAME}</string>
        <key>CFBundleIdentifier</key>
        <string>{BUNDLE_ID}</string>
        <key>CFBundleVersion</key>
        <string>{VERSION}</string>
        <key>CFBundlePackageType</key>
        <string>APPL</string>
        <key>CFBundleSignature</key>
        <string>????</string>
        <key>CFBundleExecutable</key>
        <string>launcher</string>
        <key>LSMinimumSystemVersion</key>
        <string>11.0</string>
        <key>LSApplicationCategoryType</key>
        <string>public.app-category.developer-tools</string>
        <key>NSHighResolutionCapable</key>
        <true/>
        <key>LSUIElement</key>
        <false/>
    </dict>
    </plist>
    ''')
    
    (contents_dir / "Info.plist").write_text(info_plist)
    
    # Create launcher script
    print(f"🚀 Creating launcher script...")
    launcher_script = textwrap.dedent(f'''#!/bin/bash
    # Launcher for {APP_DISPLAY_NAME}

    # Get the directory of this script
    DIR="$( cd "$( dirname "${{BASH_SOURCE[0]}}" )" && pwd )"
    RESOURCES_DIR="$DIR/../Resources"

    # Open Terminal and run the Python app with virtual environment
    osascript <<EOF
    tell application "Terminal"
        activate
        do script "cd '$RESOURCES_DIR' && source venv/bin/activate && python3 pinescript_assistant.py"
    end tell
    EOF
    ''')
    
    launcher_path = macos_dir / "launcher"
    launcher_path.write_text(launcher_script)
    launcher_path.chmod(0o755)
    
    # Copy the main Python script
    print(f"📋 Copying main application...")
    if Path("pinescript_assistant.py").exists():
        shutil.copy2("pinescript_assistant.py", resources_dir / "pinescript_assistant.py")
    else:
        print("⚠️  Warning: pinescript_assistant.py not found in current directory")
        print("   Please copy it manually to:")
        print(f"   {resources_dir}/pinescript_assistant.py")
    
    # Create requirements.txt
    print(f"📦 Creating requirements.txt...")
    requirements = textwrap.dedent('''
    torch>=2.0.0
    transformers>=4.35.0
    accelerate>=0.24.0
    huggingface-hub>=0.19.0
    rich>=13.0.0
    sentencepiece>=0.1.99
    protobuf>=3.20.0
    pillow>=10.0.0
    ''').strip()
    
    (resources_dir / "requirements.txt").write_text(requirements)
    
    # Create setup script
    print(f"⚙️  Creating setup script...")
    setup_script = textwrap.dedent(f'''#!/bin/bash
    # First-time setup script for {APP_DISPLAY_NAME}
    
    echo "🌲 {APP_DISPLAY_NAME} Setup"
    echo "=================================="
    echo ""
    echo "This will install required dependencies..."
    echo ""
    
    # Check for Python 3
    if ! command -v python3 &> /dev/null; then
        echo "❌ Error: Python 3 is not installed"
        echo "Please install Python 3.9 or later from python.org"
        exit 1
    fi
    
    echo "✓ Python 3 found: $(python3 --version)"
    echo ""
    
    # Check for pip
    if ! command -v pip3 &> /dev/null; then
        echo "Installing pip..."
        python3 -m ensurepip --upgrade
    fi
    
    echo "✓ pip found"
    echo ""
    
    # Install dependencies
    echo "📦 Installing dependencies..."
    echo "This may take a few minutes..."
    echo ""
    
    pip3 install -q --upgrade pip
    pip3 install -q -r requirements.txt
    
    if [ $? -eq 0 ]; then
        echo ""
        echo "✅ Setup complete!"
        echo ""
        echo "You can now launch {APP_DISPLAY_NAME}"
        echo ""
        touch .setup_complete
    else
        echo ""
        echo "❌ Setup failed. Please check the error messages above."
        exit 1
    fi
    
    read -p "Press Enter to close..."
    ''')
    
    setup_path = resources_dir / "setup.sh"
    setup_path.write_text(setup_script)
    setup_path.chmod(0o755)
    
    # Create app icon (basic)
    print(f"🎨 Creating app icon...")
    create_icon(resources_dir)
    
    # Create README
    print(f"📄 Creating README...")
    readme = textwrap.dedent(f'''
    # {APP_DISPLAY_NAME}
    
    ## First Time Setup
    
    1. Right-click the app and select "Show Package Contents"
    2. Navigate to Contents/Resources/
    3. Double-click setup.sh to install dependencies
    4. Wait for setup to complete
    
    ## Using the Application
    
    Double-click {APP_NAME}.app to launch.
    
    The app will:
    - Auto-download Qwen2.5-Coder-32B-Instruct (first launch only)
    - Load in Terminal with full UI
    - Store all data in ~/.pinescript_assistant/
    
    ## Requirements
    
    - macOS 11.0 or later (Apple Silicon optimized)
    - Python 3.9 or later
    - 50GB free disk space (for model)
    - 64GB RAM recommended
    
    ## Troubleshooting
    
    If the app doesn't launch:
    1. Open Terminal
    2. cd to {APP_NAME}.app/Contents/Resources/
    3. Run: python3 pinescript_assistant.py
    4. Check error messages
    
    ## Distribution
    
    To share this app:
    1. Compress {APP_NAME}.app to a .zip file
    2. Recipients must run setup.sh before first use
    3. Model will auto-download on first launch
    
    Version: {VERSION}
    ''').strip()
    
    (resources_dir / "README.txt").write_text(readme)
    
    print(f"\n✅ Application bundle created successfully!")
    print(f"📍 Location: {app_path.absolute()}")
    print(f"\n📋 Next steps:")
    print(f"   1. Copy pinescript_assistant.py to {resources_dir}/ (if not already there)")
    print(f"   2. Double-click {APP_NAME}.app")
    print(f"   3. Run setup.sh from Contents/Resources/ on first launch")
    print(f"\n💾 To distribute:")
    print(f"   - Compress {APP_NAME}.app to .zip")
    print(f"   - Recipients run setup.sh before first use")
    print("=" * 60)
    print()

def create_icon(resources_dir):
    """Create a simple app icon"""
    # Create a simple iconset (you can replace this with a proper icon later)
    iconset_dir = resources_dir / "AppIcon.iconset"
    iconset_dir.mkdir(exist_ok=True)
    
    # For now, just create placeholder files
    # Users can replace these with proper icons later
    icon_sizes = [16, 32, 64, 128, 256, 512]
    
    for size in icon_sizes:
        placeholder = iconset_dir / f"icon_{size}x{size}.png"
        placeholder.touch()
        placeholder_2x = iconset_dir / f"icon_{size}x{size}@2x.png"
        placeholder_2x.touch()
    
    # Try to convert to icns (if iconutil is available)
    try:
        subprocess.run([
            "iconutil", "-c", "icns",
            str(iconset_dir),
            "-o", str(resources_dir / "AppIcon.icns")
        ], check=True, capture_output=True)
        shutil.rmtree(iconset_dir)
    except:
        pass  # Icon creation is optional

def main():
    print(f"\n🌲 {APP_DISPLAY_NAME} - macOS App Builder")
    print(f"Version {VERSION}")
    
    try:
        create_app_bundle()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
