# PineScript Assistant Improvements Implementation Plan

## Objectives
- Implement folder-based script and screenshot management
- Add hybrid screenshot analysis approach (Option C)
- Update app bundle structure
- Improve user workflow and organization

## Implementation Steps

### Phase 1: Core Folder Management
- [ ] 1.1 Create scripts and screenshots folder structure in assistant initialization
- [ ] 1.2 Add folder listing functionality with numbered selection
- [ ] 1.3 Implement browse/select command for scripts
- [ ] 1.4 Implement browse/select command for screenshots
- [ ] 1.5 Update load commands to support folder selection
- [ ] 1.6 Add new folder management menu options

### Phase 2: Screenshot Analysis Enhancement  
- [ ] 2.1 Add configurable screenshot analysis preferences
- [ ] 2.2 Implement separate vision model analysis pipeline
- [ ] 2.3 Create hybrid analysis chooser (direct vs vision analysis)
- [ ] 2.4 Add technical report generation from vision analysis
- [ ] 2.5 Update screenshot handling to use new analysis methods

### Phase 3: User Experience Improvements
- [ ] 3.1 Add file management commands (add, move, delete)
- [ ] 3.2 Implement auto-discovery of scripts on startup
- [ ] 3.3 Add preferences for default analysis methods
- [ ] 3.4 Update help documentation
- [ ] 3.5 Test all new functionality

### Phase 4: App Bundle Updates
- [ ] 4.1 Update build_macos_app.py to create proper folder structure
- [ ] 4.2 Add sample scripts and screenshots folders
- [ ] 4.3 Update README and setup documentation
- [ ] 4.4 Test app bundle with new structure

### Phase 5: Integration and Testing
- [ ] 5.1 Integrate all components
- [ ] 5.2 Test folder-based workflows
- [ ] 5.3 Test screenshot analysis options
- [ ] 5.4 Verify app bundle works correctly
- [ ] 5.5 Final testing and documentation

## Technical Details

### Folder Structure
```
~/.pinescript_assistant/
├── scripts/
│   ├── script1.pine
│   ├── script2.pine
│   └── ...
├── screenshots/
│   ├── chart1.png
│   ├── indicator2.jpg
│   └── ...
└── [existing structure...]
```

### Menu Commands
- `scripts` - Browse and select scripts
- `screenshots` - Browse and select screenshots  
- `add_script` - Add new script to folder
- `add_screenshot` - Add new screenshot to folder
- `analyze` - Choose screenshot analysis method

### Hybrid Analysis Options
1. **Direct Analysis** - Pass screenshot directly to main model
2. **Vision Analysis** - Use Qwen2-VL for detailed analysis first
3. **Auto Choose** - Let system decide based on complexity
