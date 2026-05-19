# Claude Code Hooks

Install:

```bash
mkdir -p ~/.claude/hooks
cp hooks/claude-code/*.json ~/.claude/hooks/
export MIRROR_API_URL=http://your-mirror-instance:8844
export MIRROR_API_KEY=your-key
```

Hooks are best-effort. If Mirror is unavailable, Claude Code continues normally.
