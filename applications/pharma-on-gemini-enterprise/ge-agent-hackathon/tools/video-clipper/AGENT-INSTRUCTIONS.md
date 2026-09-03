# Agent instructions — video clipper & narrator

These instructions are for a coding agent driving this toolset on a user's
behalf. They are harness-neutral: Claude Code, Gemini CLI, Cursor, an ADK agent,
or anything else that reads repo instructions. Nothing below depends on a
specific tool API.

Human-facing docs are in [README.md](README.md).

---

## Interview first — do not start processing

**Never begin clipping or narrating on the first request.** These runs take
minutes and cost tokens, and the wrong assumption wastes both. Ask four
questions first, in one message, and wait:

1. **Inputs** — a screen recording they made, or a long recording of people
   presenting? If the latter, start at Workflow 0 and do not guess at the
   contents.
2. **Whose voice carries it** — TTS narration throughout, or do the people on
   the recording keep their own audio with TTS only framing it? If there are
   real presenters on the tape, the default is to keep them.
3. **Narration** — do they have a talk track, or should you draft one by watching
   the footage?
4. **Voice and length** — which voice (default `Puck`), and what target duration?

If your harness has a structured way to ask (a question or form tool), use it.
Otherwise ask in plain text. Then pick a workflow below.

If the footage was shot in a room rather than captured off a screen, say up
front that **the audio is probably rough** — echo, presenters drifting off-mic,
audience crosstalk. That is worth knowing before anyone forms an expectation,
and it is why a framing card is necessary rather than decorative.

---

## Workflow 0 — a long recording nobody has watched

An hour of a meeting, a conference session or a showcase is a different problem
from a screen capture the user just made. Nobody knows what is in it, and
handing the whole file to the clipper spends a long model call finding out.

**Segment by ear first.** Pull the audio and have a model listen to that rather
than the frames:

```bash
ffmpeg -i staging/raw.mp4 -vn -c:a aac -b:a 64k -ac 1 staging/audio.m4a
```

75 minutes of audio is roughly 113k tokens. The same duration sampled at 1 fps
is several times that, costs proportionally more, and *reads the room worse* —
speaker changes, names and topic boundaries all live in the audio. Ask for
segments with start/end seconds, who is speaking, what they showed, and
candidate clip windows.

**Then check those candidates on video, and only those.** A window that sounds
like a demo can be a shot of someone's back. Use an offset window instead of
re-watching the file:

```json
{"fileData": {"fileUri": "gs://…/review.mp4", "mimeType": "video/mp4"},
 "videoMetadata": {"startOffset": "3450s", "endOffset": "3710s", "fps": 1}}
```

with `"mediaResolution": "MEDIA_RESOLUTION_MEDIUM"`. Ask whether the screen
share is actually visible and legible, and let the model move the boundaries. On
a real three-segment job this pass kept one window, shifted a second (it ended
before the agent's answer appeared) and replaced a third (it opened mid-demo and
ran into Q&A). None of that is audible.

Make the review copy cheap — `-vf scale=-2:360 -r 1 -crf 32` took a 962 MB
original down to 42 MB. **Timestamps stay true to the original**, so cut the
full-resolution file at the numbers the review copy produced.

---

## Workflow A — they have no talk track

```bash
python smart_video_clipper.py staging/raw.mov staging/clipped.mp4 --duration 120
```

Produces the clipped video and `staging/clipped_talk_track.txt`.

**Stop and show the user the draft talk track before narrating.** It reliably
gets the structure right and the emphasis wrong, and it is far cheaper to fix
text than to re-render. This checkpoint is not optional.

Then continue into Workflow B.

## Workflow B — a talk track exists

```bash
python narrate_video.py staging/clipped.mp4 staging/talk_track.txt \
    --output staging/final.mp4 --retime
```

- `--retime` — Gemini watches the clip and places each line against the action.
  Use it on the first render. Skip it on re-renders when timings were already
  good; it is the slowest part.
- `--voice NAME` — override the configured voice.

---

## Two techniques that decide whether the result is watchable

**Never cut on a model's timestamps. Snap to a measured silence.** A model asked
where a sentence ends is reliably a second or two out, and a clip that opens or
closes mid-word reads as careless in a way viewers notice immediately. Where a
cut belongs is a measurable property of the audio:

```bash
ffmpeg -ss $((T-14)) -to $((T+14)) -i staging/raw.mp4 \
  -af silencedetect=n=-34dB:d=0.30 -f null - 2>&1 | grep silence_
```

Take the `silence_end` nearest the target for a **start** (speech onset — back
off ~0.25s) and the first `silence_start` at or after the target for an **end**
(speech offset — add ~0.45s). Use the model to choose *which* moment is worth
keeping; use the waveform to choose *where* to cut it.

**When the speakers are the point, narrate around them — not over them.** If the
deliverable is giving people their own session back, replacing their voice with
TTS destroys the thing that made it worth sending. What works:

```
TTS title card (~15-25s)  →  their audio, uncut  →  TTS close (~5s)
```

Render those as three separate segments and concatenate; do not mix narration
over live speech. The card carries the framing the raw audio cannot — who this
is, what they built, what to watch for.

Room audio needs real repair before it is watchable:

```
highpass=f=90,afftdn=nf=-22,acompressor=threshold=-20dB:ratio=3:attack=10:release=200,
loudnorm=I=-16:TP=-1.5:LRA=11
```

Pad the narration on both sides (`adelay` in, `apad` out) and fade the body in
and out. A concatenation seam is exactly where a clipped syllable lands.

---

## Rules

**Everything goes in `staging/`.** Inputs, drafts, intermediates, outputs. It is
gitignored and these files are large. Never write video to the repo root, and
never commit media.

**Default backend is Vertex AI with ADC.** The scripts read the project from
`gcloud config`, so usually nothing needs passing. Vertex has **no Files API** —
video is inlined and capped near 20 MB, which a real screen recording exceeds.
Set `VIDEO_STAGING_BUCKET` (or pass `--gcs-bucket`) once and oversized files are
copied to GCS automatically and deleted after. If the run fails on size and
there's no bucket, ask the user for one rather than retrying.

An API key (`GOOGLE_API_KEY`) is the alternative — Files API, 2 GB, no bucket.
A project always takes precedence over a key.

**Never hardcode a model ID.** They live in `.env`
(`VIDEO_CLIPPER_MODEL`, `VIDEO_VISION_MODEL`, `VIDEO_TTS_MODEL`,
`VIDEO_SEARCH_MODEL`). If a call 404s, the ID moved — tell the user to update
`.env`, don't patch a script.

**Distinguish 404 from 400.** A 404 means the model is gone. A 400 usually means
the ID is fine but the request shape is wrong for that model type — TTS models
need `responseModalities: ["AUDIO"]` and will 400 on a plain text probe. Do not
report a 400 as a missing model.

**Report timings honestly.** Clipping and re-timing both involve a model watching
video; these take minutes, not seconds. Say so before you start rather than
leaving the user staring at a silent terminal.

**Check the output before declaring success.** ffmpeg exits 0 on video that is
visibly broken. `narrate_video.py` self-evaluates; read its verdict rather than
the exit code. For any other render, run `python check_video.py FILE` — it exits
non-zero on a problem. Never report a render as good without one of the two.

**Budget two or three QA rounds.** First-pass renders routinely fail, and the
check earns its keep — across real jobs it caught mid-word cuts, a title
overlapping its own subtitle, and narration that ended on "let's move on to the
next step" with nothing after it.

**Read the verdicts critically — the check over-reports at seams.** The common
false positive is *"narration overlapping the presenter"* on a hard cut between
two separately rendered segments, where overlap is structurally impossible.
Treat that as a complaint about an abrupt transition — pad and fade — not as a
mixing bug to hunt. Before acting on any verdict, ask whether the defect is even
possible given how the file was built, and look at a frame yourself when that is
cheap.

**Read the last line of a generated talk track before rendering.** It reliably
invents a segue to a segment that does not exist, even when the draft it was
given ended on a conclusion. This is the single most common thing you will edit.

**Dead air fails a clip, and long-running processes are mostly dead air.** If
you are recording something that takes minutes with a near-static screen, keep
the frame alive — scroll gently through whatever is on screen during the wait —
and write the talk track around the wait instead of pretending it is not there.

---

## Failure modes worth recognising

| Symptom | Cause |
| --- | --- |
| Overlapping voices | Timing was decided before audio durations were known. Synthesise first, measure, then time. |
| Static or clipping between segments | WAV headers concatenated into the stream. Strip the 44-byte header; mux with ffmpeg `adelay` + `amix`. |
| Narration drifts out of sync after a slide | Slide duration was hardcoded instead of driven by its audio length. |
| `ffmpeg` succeeded but the video is wrong | Exit code proves nothing. Watch it, or have a model watch it. |
| Model call returns 404 | The ID moved, **or that project cannot see it.** Availability is per-project — a model that answers on one project 404s on another. Probe before concluding the ID is dead. |
| The clip starts or ends mid-word | You cut on a model's timestamp instead of a measured silence. |
| The clip drops the viewer in cold | No framing segment ahead of the footage. |
| Long stretches where nothing happens | Dead air. Keep the frame moving, or cut the wait out. |
| `ValueError: This method is only supported in the Gemini Developer client` | A Vertex client hit the Files API. Vertex has no Files API — inline the bytes or use a `gs://` URI. |
| ffmpeg exits non-zero on a clip with subtitles | The build has no libass, so the `subtitles` filter is missing. Re-run with `--no-subtitles`. |

---

## Scope

Do what was asked. If the user wants a ninety-second clip, do not also produce
subtitles, an intro slide and three variants unless they asked. Offer, don't
assume — renders are slow and unwanted ones waste real time.
