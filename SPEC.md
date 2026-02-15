# EasyTrans transcriber

EasyTrans is a simple voice memo transcription management app designed for my workflow.

## User experience

### Copying transcriptions

I use a handheld digital voice recorder connected via USB.
EasyTrans should have a “sync” option which will mount the recorder at a configured /dev location
and copy any new recordings onto the computer and add them to its database
(also at a configured location).
(I will typically delete the files directly from the recorder after a sync,
but maybe I’ll want to keep them for a while, so EasyTrans should detect any files that have been already copied and ignore them.)

Once transcription files are synced, we’ll, in parallel, in the background, create wav files in the format used by Whisper
and kick off transcription with a small model.
We’ll also annotate the files with the time they were created (from the fs),
the time they were synced,
whether they’ve been completed or handled,
and, of course, their actual transcribed text, timestamp-annotated.
There will also be an option to manually edit the text later, so that should be stored separately.
Also, I expect I’ll eventually want an option to re-transcribe with a more powerful model
if the initial tiny/fast model does a bad job, so it should be possible for there to be multiple automatic transcriptions.


### Viewing and handling transcriptions

The main UI will be a simple one-screen CLI (maybe with a couple modals eventually), and will look something like an email app,
with a table view of memos at the top (ID number, completion status, date and time recorded, first line of the text)
and a preview pane showing the selected item below.

The following actions will eventually be available:

* Sync from voice recorder (described above)
* Transcribe file (you can use this to one-off add a random audio file to the database, maybe something you recorded with a different app)
* Show/hide completed transcriptions
* Edit text (opens the md file in an external editor)
* Retranscribe with larger model
* Copy text to clipboard (with or without timestamp annotations, default without; probably separate keybinding for with)
* Mark complete (removes from the list unless you show completed transcriptions; the app opens with them hidden)
* Play Audio (probably something where it highlights the timestamp annotations and lets you scroll through them and tap where you want to hear; might need to try to integrate this with the editor).

We’ll also eventually have integrations which will let you automatically do things with the cleaned-up transcriptions:

* send to to-do list
* send as email
* add to end of Random Thoughts document
* add to dream journal
* add/edit Mosaic Muse entry

...but these will come later; I tried to design a prototype of this a while back and got hung up on trying to figure out the integrations before I actually had a working transcription flow, and it’s clear that having to copy-paste the results is a pretty minor issue compared to actually having working transcription software!

I might also eventually consider having LLM-based automatic classification and/or cleanup, but that’s getting even further ahead of ourselves (and amusingly, looking back on it now, I also tried to do this first in the first prototype).


## Data model

Ultimately I’d like to turn transcribed voice memos into text files in a way that works transparently.
To make integrations really easy, we’ll ultimately store our data on the file system like this.

transcriptions/
    audio/
        2025/
            ...
        2026/
            2026-0001.mp3
            2026-0001.wav
        ...
    text/
        2025/
            ...
        2026/
            2026-0001.md
            2026-0001.versions
        ...

Filenames of transcribed files are a YYYY-{sequential identifier} format,
where YYYY goes by the date the memo was recorded (rather than transcribed).
Since needing more than 4 digits would require me to record more than 27 voice memos per day on average,
which seems extremely excessive,
that much zero-padding seems sufficient to ensure proper sorting.

The .md file is the file the user will interact with most of the time.
It will contain the current version of the transcription, and the user can edit it at any point.

Obviously, the .mp3 (or this can actually be any extension other than .wav - whatever the voice recorder we’re using is configured to record) is the original file,
and the .wav is the version we convert during sync necessary with ffmpeg or whatever.

We’ll also have a SQLite database that stores metadata for each memo:

`memos` table

* hash of the original file (used as primary key), so that we can determine whether a file, whatever it’s called on the voice recorder, is new or already transcribed
* file ID (string) in the format shown above (2026-0001)
* date/time of recording (based on the fs timestamp of the file)
* completed? (bool)

`transcriptions` table

many-one relationship with the `memos` table, each time a transcription is made using a different model it lands an entry in here

* id integer pk
* date/time of transcription
* enum key for name of ai model used
* text


## Implementation choices

We’ll write the app in Python using a modern dev stack:

* uv
* Pytest
* Pyright for type checking & linting
* sqlite with SQLAlchemy
* Textual

