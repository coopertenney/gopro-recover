# gopro-recover

Get deleted or formatted videos and photos back off a GoPro SD card, including the
case that defeats PhotoRec and friends: **the main 4K clip is interleaved on the card
with its low-resolution LRV sidecar**, so a plain file carver returns MP4s with no
index and video data that stops mid-frame.

## The app (no terminal needed)

1. Go to **Releases** (right-hand side of this page), download **GoPro-Recover.zip**, and unzip it.
   (Use that ZIP, not GitHub's "Download ZIP" button, which strips the launcher's permission to run.)
2. Double-click **GoPro Recover.command**. The first time, macOS will say it can't verify
   the developer:
   - macOS 14 or earlier: right-click the file, choose **Open**, then **Open** again.
   - macOS 15 or later: open **System Settings → Privacy & Security**, scroll down, click
     **Open Anyway**, then double-click the file again.

   It then checks for Python 3 (macOS offers a one-click install if it's missing), fetches
   ffmpeg once (about 80 MB, needs internet), and opens the app in your browser.
3. Follow the five steps on screen: insert card, scan, filter, preview, recover.

What it does, step by step:

- **Card.** Pick the card from the list. You enter your Mac password once so the card
  can be read directly; nothing is ever written to it.
- **Scan.** One pass over the whole card, about 12 minutes per 64 GB. Finds every video
  index and photo that survived the format.
- **Filter.** Videos or photos; shortest and longest length; resolution; camera model;
  date text.
- **Results.** Each clip shows length, resolution, camera, size, and how much of it is
  intact. **Preview** builds a sheet of frames from start to end so you can recognise
  footage by eye. Tick what you want.
- **Recover.** Pick a folder. Files are rebuilt as normal MP4s or JPEGs.

Requirements: a Mac with Python 3 (macOS offers a one-click install if it is missing)
and a card reader. Linux works with `python3 app.py` and ffmpeg installed.

## How it works

GoPro writes each recording as two files at once, the `.MP4` and a `.LRV` preview, so
the card ends up with the big clip's blocks interrupted every half-second or so by a
burst of sidecar blocks. After a format the directory is gone, but the data and the
MP4 index (`moov` atom) are still there.

1. **`scan.py`** reads the whole card once and finds every `moov` index and every
   `ftyp` file header. The index tells you the clip's duration, camera clock, resolution,
   firmware, and the exact byte size of every video frame, audio chunk, and GPMF
   metadata sample.
2. **`deint.py`** walks the card block by block starting at the clip's header, checking
   each block against what the index predicts should be there: every H.264 / H.265 frame
   starts with a length-prefixed NAL header, every GoPro metadata sample starts with
   `DEVC`. Blocks that match are the clip. Blocks that match the sidecar's own index,
   or that carry an access-unit delimiter where none is expected, or that start with a
   JPEG header, are skipped. It evicts bad tentative blocks and resyncs if it loses the
   thread (e.g. the clip's first second was overwritten).
3. **`preview2.py`** maps every clip over a minimum length and streams a moov-first
   copy straight into ffmpeg to produce one thumbnail contact sheet per clip, so you can
   identify footage by eye without extracting gigabytes.
4. **`extract2.py`** rebuilds the chosen clip as a normal, byte-exact MP4.

Verified on HERO9 (H.264), HERO10 (H.265) and HERO11 footage, 64 GB cards with 128 KB
clusters and 32 GB cards with 32 KB clusters. A fully intact clip extracts with zero
decode errors and the original file size to within a few hundred bytes of trailing
padding.

## Command-line use

```bash
brew install ffmpeg
# card shows up as /dev/disk4; give yourself read access to the raw device
sudo chmod 644 /dev/rdisk4 /dev/disk4
diskutil unmountDisk /dev/disk4

export CLUSTER=131072          # exFAT cluster size in bytes; exfat.py prints it
python3 scan.py /dev/rdisk4 card1          # ~12 min per 64 GB, writes ~/Desktop/GoPro_moov_card1/
python3 preview2.py /dev/rdisk4 card1 45   # sheets for clips >= 45 s -> ~/Desktop/GoPro_clips_card1/
python3 extract2.py /dev/rdisk4 card1 17 ~/Desktop/clip17.mp4
```

`ONLY=4,5 python3 preview2.py …` limits to specific clip numbers. `python3 exfat.py
/dev/rdisk4 <partition_start_sector>` lists the directory including deleted entries,
which is the fast path when files were deleted rather than formatted.

Other helpers: `claim_heads.py` matches every index to its file header and lists
headers nobody claims (recordings the camera never finalized); `devc_census.py` and
`devc_locate.py` count and locate a clip's metadata markers across the card to tell
whether its data still exists.

## Identifying what you found

Camera clocks on GoPros reset after a battery pull, so dates lie. Two things don't:

- **File size.** A rebuilt clip's size (`mdat` end + `moov` size) matches the original
  to within about 200 KB.
- **Index fingerprint.** The `stsz` sample-size tables inside the `moov` are unique
  per recording. If you still have a copy of some clips, hashing their tables against
  the recovered indexes tells you exactly which recovered clips you already own.

## Limits

- Needs the `moov` index to survive. Recordings the camera never finalized have none;
  `claim_heads.py` finds their headers but rebuilding them needs a reference index.
- If later recordings overwrote part of a clip, the walker resyncs past the gap and
  the extracted file plays with that stretch missing.
- Python reads the card at 80 to 90 MB/s; expect about 25 minutes per 64 GB card for
  scan plus previews.

MIT licensed.
