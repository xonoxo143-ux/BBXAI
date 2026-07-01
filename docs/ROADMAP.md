# Roadmap

This roadmap is intentionally aimed at the wow moment, not a tiny checklist product.

## Stage 0 — Housekeeping

Goal: keep the repo clean and directionally correct.

- README states the product identity
- docs capture the human/AI boundary
- repo structure separates planning, engine, Android, samples, and output
- no old project assumptions are copied in

## Stage 1 — Audio engine scratchpad

Goal: prove that beatbox samples can be inspected and described.

Possible work:

- accept local WAV files
- detect duration
- estimate loudness
- detect onset/attack sharpness
- estimate rough pitch range
- produce a small JSON report

Likely tools:

- Python
- librosa
- soundfile
- numpy

## Stage 2 — Manual-label arranger

Goal: prove arrangement before perfect AI classification.

The user can manually label sounds as:

- kick
- snare
- hi-hat
- bass
- texture
- vocal

Then BBXAI arranges them into a short structured beat.

This stage is valid because the project promise is arrangement, not perfect automatic labeling.

## Stage 3 — First wow test

Goal: hear user-made sounds become something track-like.

Acceptance test:

> I record my own sounds, arrange them with BBXAI, and feel like I actually made music.

## Stage 4 — Optional AI assistance

Goal: add smarter help without taking away user control.

Possible work:

- suggest labels
- suggest missing sounds
- suggest genre templates
- suggest EQ/volume fixes
- suggest arrangement variations

## Stage 5 — Android shell

Goal: phone-first experience.

Possible flow:

```text
Record/select sounds
→ send to engine
→ arrange
→ preview result
→ save/export
```

Android work should not outrun the engine’s ability to produce the wow moment.
