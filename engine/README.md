# Engine

This folder is for Python/audio experiments.

Early engine goals:

1. Load local WAV files.
2. Analyze basic audio features.
3. Produce simple reports.
4. Arrange manually labeled sounds into a short beat.
5. Keep experiments small and understandable.

## Likely starting tools

- Python
- librosa
- soundfile
- numpy

## Early rule

Do not start with a giant AI model.

Start with a clear pipeline:

```text
input samples
→ analyze
→ label or confirm labels
→ arrange
→ output audio/report
```

## First engine success

The engine succeeds when it can help create a result that feels like the user's own sounds became music.
