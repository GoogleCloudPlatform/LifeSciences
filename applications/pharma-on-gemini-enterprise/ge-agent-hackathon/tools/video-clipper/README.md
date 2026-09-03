# Video clipper & narrator

Turn a raw screen recording into a short, narrated demo video — using Gemini to
choose the clips, write the talk track, and speak it.

Built for the problem every hackathon ends with: **you have twenty minutes of
messy screen capture and you need ninety seconds that someone will actually
watch.**

It handles the other version of that problem too: **an hour of a recorded
session where several teams presented, and you want to give each of them their
segment back.** That path keeps the presenters' own audio and uses TTS only to
frame it — see [Long recordings](#long-recordings-you-havent-watched).

```
raw recording ──▶ clip to length ──▶ talk track ──▶ TTS + sync ──▶ narrated demo
                  (Gemini watches)   (generated)    (visual-timed)
```

---

## What each script does

| Script | Does |
| --- | --- |
| `smart_video_clipper.py` | Gemini watches your raw recording and picks the moments worth keeping, cut to a target duration. Also writes a first-draft talk track. |
| `narrate_video.py` | Generates TTS narration and **times it to what's on screen**, so the voice matches the action. Guarantees no overlapping speech. |
| `search-video.py` | Ask questions about a **YouTube** video's contents. Takes a URL, not a local file. |
| `check_video.py` | Has a model watch a finished render and report stalls, audio corruption, overlapping narration. Exits non-zero on FAIL. |
| `video_source.py` | Shared helper: gets a video into a request on whichever backend you're using. Not run directly. |

---

## Setup

```bash
uv sync                                 # Python deps, from pyproject.toml
brew install ffmpeg                     # or: sudo apt install ffmpeg
cp .env.example .env                    # then add your key / project
```

Run the scripts with `uv run`, which uses that environment without you having to
activate anything:

```bash
uv run smart_video_clipper.py --help
```

**Auth — Vertex AI with ADC is the default:**

```bash
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

The scripts read the project from `gcloud config`, so no flag and no env var are
needed. Override with `--project` or `GOOGLE_CLOUD_PROJECT`.

**Set a staging bucket.** Vertex has no Files API — video is inlined into the
request, which caps it near **20 MB**. A real screen recording is bigger than
that. Give it a bucket and oversized files are copied there automatically and
deleted afterwards:

```bash
export VIDEO_STAGING_BUCKET=your-bucket    # or pass --gcs-bucket
```

You can also pass a `gs://` URI directly as the input path.

<details>
<summary>Alternative: a Gemini API key</summary>

Set `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) from
[AI Studio](https://aistudio.google.com/apikey) and leave the project unset — a
project always takes precedence. This path uses the Files API, which handles up
to 2 GB with no bucket, but it bills to a personal key rather than your project.

| | Vertex (`--project`, default) | API key |
| --- | --- | --- |
| Transport | inline, or GCS over ~20 MB | Files API |
| Practical limit | bucket-sized | 2 GB |
| Needs a bucket | for real recordings, yes | no |

</details>

---

## Use it

**1 — clip the recording and get a draft talk track**

```bash
python smart_video_clipper.py staging/raw.mov staging/clipped.mp4 --duration 120
# writes staging/clipped.mp4 and staging/clipped_talk_track.txt
```

**2 — edit the talk track.** Always. The generated draft gets the structure
right and the emphasis wrong. This is two minutes of work that makes the
difference.

**3 — narrate it**

```bash
python narrate_video.py staging/clipped.mp4 staging/clipped_talk_track.txt \
    --output staging/final.mp4 --retime
```

`--retime` makes Gemini watch the clip and place each line where it belongs.
Use it on the first render; skip it on re-renders if timings were already good.

`narrate_video.py` self-evaluates its own output. To re-check a render at any
time — or one you produced some other way:

```bash
python check_video.py staging/final.mp4     # exits 1 if it finds a problem
```

Keep inputs and outputs in `staging/` — it's gitignored, and these files get
large.

---

## Long recordings you haven't watched

A screen capture you just made and an hour of a recorded session are different
problems. For the second one, don't hand the whole file to the clipper — find
the moments first, cheaply.

**1 · Segment by ear.** Pull the audio and have a model listen to it:

```bash
ffmpeg -i staging/raw.mp4 -vn -c:a aac -b:a 64k -ac 1 staging/audio.m4a
```

Then ask a model for every distinct segment — start and end seconds, who is
speaking, what they showed, and the best 90–180s window for each. No bundled
script does this yet; `search-video.py` takes a YouTube URL, not a local file.
Your agent harness makes the call, and `video_source.resolve_video()` will hand
the file over correctly on either backend (it knows audio MIME types, and stages
to GCS when the file is over the Vertex inline cap).

75 minutes of audio is about 113k tokens. The same duration as video sampled at
1 fps costs several times that — and reads the room worse, because speaker
changes and topic boundaries live in the audio.

**2 · Check only the candidate windows on video.** A window that sounds like a
demo can be a shot of someone's back. Confirm the screen share is visible and
legible before you cut.

**3 · Snap the cut points to real silences.** This is the difference between a
clip that feels edited and one that feels chopped:

```bash
ffmpeg -ss $((T-14)) -to $((T+14)) -i staging/raw.mp4 \
  -af silencedetect=n=-34dB:d=0.30 -f null - 2>&1 | grep silence_
```

Start on a `silence_end` (speech beginning), end on a `silence_start` (speech
ending). A model's guess at a sentence boundary is usually a second or two out,
which lands you mid-word.

**4 · Keep their voices.** When real people are presenting, the structure that
works is a TTS title card, then their audio untouched, then a short TTS close —
rendered as three segments and concatenated, never mixed over each other.

Room audio almost always needs repair first:

```
highpass=f=90,afftdn=nf=-22,acompressor=threshold=-20dB:ratio=3:attack=10:release=200,
loudnorm=I=-16:TP=-1.5:LRA=11
```

---

## Models

**All model IDs live in `.env`, not in the code.** Gemini IDs move: `-preview`
suffixes are withdrawn at GA, and a model that worked last month can 404 today.
When that happens, edit `.env` — you should never need to touch a script.

| Variable | Default | Used for |
| --- | --- | --- |
| `VIDEO_CLIPPER_MODEL` | `gemini-3.1-pro-preview` | Watching raw footage, deciding what to keep |
| `VIDEO_VISION_MODEL` | `gemini-3.6-flash` | Timing narration to on-screen action |
| `VIDEO_TTS_MODEL` | `gemini-3.1-flash-tts-preview` | Speech (24 kHz mono PCM) |
| `VIDEO_SEARCH_MODEL` | `gemini-3.5-flash-lite` | Cheap video Q&A |
| `VIDEO_TTS_VOICE` | `Puck` | Voice. Also: Charon, Kore, Fenrir, Aoede, Sulafat |

### Checking a model still exists

Faster than reading release notes:

```bash
curl -s -X POST \
  -H "Authorization: Bearer $(gcloud auth application-default print-access-token)" \
  -H "x-goog-user-project: $PROJECT" -H "Content-Type: application/json" \
  "https://aiplatform.googleapis.com/v1/projects/$PROJECT/locations/global/publishers/google/models/MODEL_ID:generateContent" \
  -d '{"contents":[{"role":"user","parts":[{"text":"hi"}]}],"generationConfig":{"maxOutputTokens":8}}'
```

**404** means the ID is gone — find its replacement. **400** usually means the ID
is fine but the call shape is wrong for that model type (TTS models need
`responseModalities: ["AUDIO"]`, so a plain text probe returns 400, not 404).
Don't mistake one for the other.

---

## Six things that were learned the hard way

Worth knowing if you build anything similar.

**1 · Generate the audio *before* deciding the timing.** The obvious order — ask
a vision model when each line should start, then synthesise — produces
overlapping speech, because a visual cue can be shorter than the sentence
describing it. Instead: synthesise everything first, measure each clip's real
duration, and pass those as a hard budget (`start[i] + duration[i] <= start[i+1]`).

**2 · Strip the WAV header before concatenating.** Gemini TTS returns WAV.
Joining the byte strings injects a 44-byte header into the middle of the audio —
which you hear as static. Strip it (`pcm[44:]`), and use ffmpeg's `adelay` +
`amix` to place clips on a timeline rather than padding bytes by hand.

**3 · Have a model watch the finished video.** ffmpeg reports success on output
that's visibly broken. A final pass checking for frozen video, audio corruption
and dead air catches things a return code never will.

**4 · Slide duration follows audio duration, not the other way round.** Hardcode
six seconds per slide and the ten-second narration runs over the next one.
Measure the audio, then write that duration into the ffmpeg concat list.

**5 · The two Gemini backends are not drop-in for video.** `client.files.upload()`
raises `ValueError: This method is only supported in the Gemini Developer client`
on a Vertex client — Vertex has no Files API, only inline bytes and `gs://` URIs.
Code written against an API key silently assumes a transport that doesn't exist
on the other backend. `video_source.py` is the shim.

**6 · Check `ffmpeg -filters` before you rely on one.** Subtitle burn-in needs
the `subtitles` filter, which needs libass, which plenty of builds omit. The
failure arrives at render time — after two model calls and several minutes — as
a bare non-zero exit. A one-line capability probe up front turns that into a
warning.

---

## For agent harnesses

`AGENT-INSTRUCTIONS.md` in this directory is written to be read by a coding agent — Claude
Code, Gemini CLI, Cursor, or anything else that picks up repo instructions. It
covers the interview to run before starting and the two workflows.

Nothing here is harness-specific: these are plain Python CLIs. An agent is a
convenience, not a requirement.

---

Apache-2.0, same as the rest of this repo. Not an officially supported Google
product.
