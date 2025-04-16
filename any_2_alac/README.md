# Any 2 ALAC

This is a utility I use for taking my mostly-flac library and converting it to an ALAC file with
soundcheck data, suitable to importing into Apple Music and converting down to 256kbit AAC.

It uses the mutagen library (pip install mutagen) for moving the tags from the original files to
the newly-minted ALAC files.

N.B.: the alac files are huge because they are float-based... I'm not sure if there's another afconvert
option that would make smaller ALAC files.  But, since they are just a step before 256kbit AAC, I don't
care too much.

After importing to Music (with wifi off so it doesn't add it to my library), I use File -> Convert to 
convert them to AAC, and then I delete the ALAC files completely.

