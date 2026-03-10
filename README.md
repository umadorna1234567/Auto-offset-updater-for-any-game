the app.py is just for source code or if you want to change it but you can run it from it tho but i added an .exe so its just a double click 

also this is just for offsets not sigs im working on sigs now and multi file support this readme includes sig stuff as well if i havent updated it yet disregard those parts

WHAT TO KNOW BEFORE STARTING 
OFFSETS  1. if your using it just for offsets use the dumper of your choosing it should work depending if the offset names are the same in both code and dump
SIGNATURES/PATTERNS 2. If your finding sigs dump the game with ida pro then click File → Produce file → Create LST file and that is what you will parse through



HOW TO USE

----1 OFFSET SEARCH
if you need to find just 1 offset use the file search tab and input game dump folder or browse for it then type in the text you want to search the files for then click search

----MULTI OFFSET AUTO UPDATE WITH A DUMP OF THE GAME (1 file)
if you want to auto update all the offsets in 1 file go to the offset updater tab and copy all of the code from your projects offsets file into the old offsets input box then input the directory or browse for your game dump folder then click find new offsets

----MULTI OFFSET AUTO UPDATE WITH A DUMP OF THE GAME (2 or more files)
if you want to auto update all the offsets in multiple files at once go to the offset updater tab and toggle on file mode and click select files to update and go to the folder and hold ctrl and click or drag over the files for offsets or patterns (signatures ect) in them then input the directory or browse for your game dump folder then click find new offsets 

----MUTI OFFSET AUTO UPDATE WITHOUT DUMP
the same steps as the previous ones just instead of putting in the folder directory or browsing for a folder with a game dump you click the Use DumpSpace API toggle and instead of finding the offsets in a dump on your pc it will find them from https://dumpspace.spuckwaffel.com and then type in the games name into the game name box at the top (make sure you spell it right capitol letters dont matter) then click find new offsets and note how long ago the dump was updated after you click find new offsets it will show up above the updated offsets output box
