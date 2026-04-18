---
Task ID: 1
Agent: main
Task: Fix venv UI refresh, import button behavior, and AI forward button

Work Log:
- Analyzed terminal.js code structure: venv functions, import, forward-to-AI
- Issue 1: createVenv() called loadVenvInfo() immediately after POST, before streaming finished
  - Fix: Added onProcessComplete callback mechanism, loadVenvInfo() now runs after streaming ends
- Issue 2: importRequirements() ran directly without confirmation or UI changes
  - Fix: Added confirm dialog, closes left sidebar, expands console panel, starts cmd block
- Issue 3: "发送给AI" button existed but sidebar toggle used non-existent 'toggle-right' element
  - Fix: Changed to use 'btn-chat' click, also added startCmdBlock to file execution runs
- All changes committed and pushed to ctz168/ide main branch

Stage Summary:
- File modified: static/js/terminal.js (55 insertions, 7 deletions)
- Commit: 3c06f91
- Push: successful to origin/main
