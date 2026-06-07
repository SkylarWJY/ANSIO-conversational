# Recording the demo GIF

`ui-preview.png` is a real headless screenshot of the running console (idle
state). For an animated `demo.gif` of a live voice turn, record the running app
and drop the file in next to this one as `assets/demo.gif` — the README picks it
up automatically.

## 1. Start the stack
```bash
pm2 start ecosystem.config.cjs    # web(8788) + token + agent worker
# or: cd agent-py && uv run python src/agent.py dev   +   uvicorn token_server:app --port 8788
open http://localhost:8788/        # Chrome; grant the mic when prompted
```

## 2. Record a ~8s turn
- Click **Talk to ANSIO**, grant mic, ask: *"Find me underpriced creators for an AI coding tool."*
- Capture the screen region (macOS `Cmd+Shift+5`, or QuickTime screen recording) while the
  conversation bubble appears (left = ANSIO, right = you) and the right-hand
  evidence cards fan in with millisecond HUD numbers.
- Keep it short and loopable — the recall chain lighting up is the money shot.

## 3. Convert to an optimized GIF
```bash
# from a .mov/.mp4 screen recording:
ffmpeg -i recording.mov -vf "fps=15,scale=1200:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" -loop 0 demo.gif
# target < ~6 MB so GitHub renders it inline
```

## 4. Regenerate the static preview (optional)
```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu \
  --hide-scrollbars --force-device-scale-factor=2 --window-size=1440,860 \
  --screenshot="ui-preview.png" "http://localhost:8788/app/index.html"
```
