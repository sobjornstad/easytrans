## Environment

You are developing in a Vagrant VM with --dangerously-skip-permissions.
Feel free to customize this machine to your needs, install software, etc.

A simulation of what the relevant parts of the voice recorder filesystem
will look like once mounted is available in /media/recorder.
Do not edit these files, but copy them as needed.
RECORDER/FOLDER_B will be the user's chosen location for memos transcribed by this tool
for our testing purposes (this will be a config option).
I've filtered this simulation to contain only short memos
since you'll have limited compute resources to run Whisper on this machine.

(Go ahead and implement the code/config that mounts/unmounts the recorder,
 but comment out the call to those functions for the moment for testing.
 Once the main development is done I'll uncomment it and test that part on the real machine.)


## Testing

Interactively test your changes as you go using the `tmux-tui` skill.
Also write unit tests of the data/mechanical layer
and UI tests using Textual's UI testing framework,
that verify the expected behavior.

If during development you encounter a bug that could plausibly be re-introduced,
add a failing regression test before fixing it.
