# Changelog

## Next

### Metadata
- The viewer's Info panel is open by default and shows **all** the metadata of a photo or video, always under the basics: every EXIF tag with its name and its meaning in words (flash, metering, white balance...), the full GPS block, IPTC and XMP written by other programs (titles, keywords, ratings, copyright), the text inside a PNG, the colour profile, the RAW sensor data, and for a video its container, every stream (codec, profile, size, frame rate, colour range, bit rate, audio) and its tags. It can be searched and copied. Closing the panel is remembered.

## 2.0.6

### Sidebar
- The foot of the sidebar says what the library takes and how much space is left on the disk ("41.2 GB used  ·  212 GB free"), instead of the size of the whole disk; the meter shows how full the disk is

### Search
- The suggestions understand several words ("viaje disney" offers the folder "Viaje Disney 2018"), list the folders first and then the albums, and a choice replaces all the words typed
- A search that is only a folder (its name, or the words of it: "viaje disney", "viaje a disney 2018") shows the folder with its albums as cards, as clicking it in the sidebar does; typing the name of one album ("epcot") goes straight to its photos
- Typing the name of a folder of the sidebar ("viaje disney") lists the folder and, under it, the albums and folders it holds (Epcot, Magic Kingdom, Disney Springs...) to choose from
- When a name is both an album and a folder, the suggestions list each apart ("Familia · Album", "Familia · Folder") and choosing one searches only that, while the box shows just the name, no quotation marks or prefixes (`album:Familia` and `folder:Familia` can also be typed); typing the name alone finds both
- Choosing a suggestion that has several words (an album, a folder) no longer puts it between quotation marks, which made it be looked for as plain words instead of as the album
- Search by album, by folder of the sidebar (it finds the photos of every album inside it) and by folder on disk (`Cumpleaños`, `folder:Abuelos`, `album:Embarazos`), and by dates the way they are written: `15/07/2019`, `15 de julio de 2019`, `4 julio` (any year), `antes de 2012`, `después del 2020`, `desde marzo 2023`, `hasta 2013`, `entre 2015 y 2017`; `-Ana` leaves a person out and `-videos` the videos
- Searching for a person was slow (9 to 15 seconds for a person with thousands of photos) and the list showed only the first photos while the rest took their time; it is now immediate. A search typed inside a person looks through the whole library. A page of photos that could not be read is tried again instead of ending the list there
- **The search box understands what you type**, in Spanish or English: a person (`Ana`, `Ana y Luis` for the photos with both), a year, a month and a year, a range (`2019-2021`), `ayer`, `el año pasado`, `lunes`; a place (`Miami`, `Dominican Republic`, named from the photo's coordinates with no network); a kind (`videos`, `capturas`, `favoritas`, `editadas`, `vertical`, `4k`, `raw`, `png`); a camera (`iphone`, `canon eos`); `5 estrellas`, `mayor de 10 MB`, `ISO 800`; `-word` takes words away. What it understood shows as chips under the bar, each with a cross that takes it out, and what it did not is looked for as words, as before
- Suggestions while you type: people, places, months, years and cameras of your own library. Down and Up choose, Tab or Enter completes, Escape closes
- Words are compared without accents and capitals: `cancion` finds `canción`
- Searching from People goes to the photos

### First steps
- Photos can stay on an external drive: choose "A Drive With Your Photos" in the first steps, or "Add a Drive…" in Settings, and Piklin works with them where they are, never copying or moving them. The list, previews, albums, edits and People stay on the computer, so with the drive unplugged you can still browse, search and use the map; the footer says which drive is missing, clicking a photo says its original is on the drive, and when the drive comes back Piklin looks for new photos on its own
- The window of the first steps (language, library, photos, backup, ready) is more even: the choices are cards of the same height, with the same space between them (the last had more below it), a round tile on the left and a round check on the right, a little more type; the numbered dots are further from the lines between them; Back and Continue are a pair of pills of the same size and shape, one outlined and one black; and there is the same air above the content as below it. With the choices a little taller, all four of "Keep a Copy of Your Photos" still fit

### People
- **People, without a cloud.** A new People row in the sidebar. Turn it on and Piklin looks for the faces in your photos and groups the ones that are alike; you name each person and open every photo of them. It all happens on this computer: no photo and no face is sent anywhere, and turning People off deletes everything it made. It is off until you ask for it
- It looks while the computer is plugged in and idle, a few photos at a time, so the first people appear soon and the rest follow. Hidden and deleted photos are left out
- The first people appear within seconds of turning People on: the first photos are looked at at once, whatever the computer is doing, and the faces are grouped every few dozen photos at the start. After that it looks while the computer is plugged in and idle. A library is looked at about three times faster than a first version managed (photos are opened side by side, and one without a face is dismissed from a small copy of it)
- Each person's picture is a face that looks at the camera and is upright, and a tilted head is turned straight. The menu of three dots turns the picture to the left or the right if a face is still on its side
- Piklin asks "Is this the same person?" about two groups that look alike but not enough to join them by themselves - never two people who are in one photo, which cannot be one person - and remembers a No
- **People is shared through the backup.** Each computer writes its own file of People into the library (the faces, with the numbers that say whose they are, the people, their names and what you decided), the backup carries it to the NAS or the cloud and the other computers read it and merge it with theirs; nobody writes another's file. A computer that has the same photos takes the faces as they are, so it does not look at them again, which is the slow part. The names and decisions are the same everywhere, the newest decision wins, and people you joined on one computer are joined on the others. Photos are matched by what is in them, not by where they are kept
- The file of People never reached the NAS or the cloud: the backup read only the Edits, Albums and Originals folders at the destination, so the People folder was sent but never seen by the other computers. It is read now, and a computer that already had people when it was updated writes its file once when Piklin opens, instead of waiting for the next change
- Opening People, and coming back to it from a person's photos, no longer holds the window still: with two hundred people it did for several seconds. The cards are made a few at a time, their menus when opened, their pictures by two workers and kept (in memory and in the library's cache), and the list is not built again when nothing changed. Turning a person's picture changes only that picture, and the list stays where you were, also when a name or a join rebuilds it
- People is for photos only: no video is looked at, and none is taken in from another computer
- **Faces lying on their side or upside down are found, with nothing to press.** The detector does see them, but less surely and with the eyes and the mouth in the wrong places, so the numbers it gave were worthless (a face turned a quarter had a likeness of 0.04 with the same face upright) and it also took a button for a face. When a photo has nobody in it, or only doubtful faces, or one that is turned over, Piklin looks at it turned each way, keeps the reading that is surest in each place, and reads the face as if it were upright. A baby lying on its side is now found, the avatar is cut from the face upright, and the photos looked at before are looked at again in the background, only the ones with nobody in them or with doubtful faces. It costs about half as much again to look at a photo, and finds about 5 % more faces
- **A person known well takes what is clearly theirs, by itself.** A person with hundreds of faces is known by their nearest faces, not only by the average: a new face that is clearly more theirs than anybody else's well known person goes under their name without anything to press, whether it is looked at now or was left in no group. Checked on a library of 19 000 faces: right 99.5 times in 100, and it is not taken as confirmed, so Not This Person still takes it out. A face that is about as alike to two people as to each other is put in neither. It happens once when Piklin opens and after each scan, and on the library it put 363 of 1 821 loose faces in their person
- **Every person is looked through for faces that do not belong.** Once Piklin opens and when a scan ends, each person's faces are compared with the faces of the same person from other moments (the photos of one burst vouch for each other and hid every misplaced face) and with the other people's: a face clearly more another person's goes to them, one like nobody's goes loose to be grouped again. Only faces Piklin put there are moved, never ones confirmed one by one. Faces you chose one by one in Is this the same person? are never moved, and those confirmed only by naming the group are moved only when it is very clear. On a library of 19 000 faces it moved 62, because most faces that do not belong are look-alikes (a sister, a mother), which a face recognition model cannot tell apart; those are for Not This Person
- Going back from a photo to a person's photos no longer says there are no photos: the list was loaded again without the photos of the person (the same happened with the photos of a place on the map). It is now loaded with the person's photos as they are at that moment
- The list of people is made a few milliseconds at a time, by a timer: with the window busy (thumbnails, the scan, the backup) the cards were left half made, and People looked as if it had almost nobody when coming back from a person
- **Find More People.** A button in People looks again at the faces that are in no group, a little less strictly: it puts them in the people there are at less likeness, and makes a person of faces that are alike in two photos and not only in three. People says how many faces are in no group yet. It also looks at any photo not looked at yet. On a library of 19 000 faces it found 69 more people and put a thousand faces in a group
- **Choose the picture of a person, and frame it.** In a person's photos, right-click a photo and choose Use as Picture (or Choose Picture in the person's menu, which takes you there): a window opens over the photo with a circle on the person's face, to be dragged onto the face, made larger or smaller with the wheel or the slider, and the photo turned if it is on its side. Piklin does not always find the face (it took a button for one, and does not find a face lying down), and this does not depend on it. The picture is kept, travels to the other computers and is not replaced by Piklin's own
- Two groups with the same name are one person: naming a group like another that already has that name joins them, and groups already named alike are joined
- While you use the window, People looks at one photo at a time and gives way; when you do not, it uses what the computer can spare. A first look at a small copy of each photo is skipped when most photos have people in them, which cost more than it saved. The work is a quarter lighter for the same photos
- The AV1 videos of a library are no longer tried first with an OpenCV that cannot read them: once one has failed, the next go straight to the player Piklin carries, and its log no longer fills with errors
- **Piklin learns from what you tell it.** Faces you confirmed (by naming a person, or by picking them in "Is this the same person?") become anchors: a new face joins a person it is close to any of those, not only to their average, which is what a child in two poses needs. A face you took out of a person, and the faces just like it, do not come back to them. And from your answers to "Is this the same person?" Piklin learns how alike two groups have to be, for you: when you nearly always say yes at a likeness it joins them from there and stops asking, and when you always say no it stops asking about pairs that alike
- "Is this the same person?" is a big card with eight faces of each group: click the faces that are the same person, then click Yes (Select All takes every face). Only a face of each group joined together moves them, and what you did not pick stays where it was
- The groups are suggestions, and Piklin would rather split one person in two than mix two people. A person's menu names them, joins them with another or puts them away; on a person's photos, Not This Person takes a photo out of them, and it is never put back by itself. Babies and small children are the hardest to tell apart, and the same child at two ages often is two people, so joining them is one click

### Viewer
- Zoom out, zoom in and fit-to-window buttons in the bar of an open photo, and the + - 0 keys; zooming in no longer stretches the photo
- A person with thousands of photos scrolls without flicker: from 1,500 photos their list is one run of rows of the same height, without day headings (the list could not tell how long it was until it had seen every row, and the view jumped back and forth by whole screens)
- Going back from a photo returns to that photo in the list (in a person's photos too), not to the top; and a refresh in the background (a scan ending) no longer sends a long list back to the top

## 2.0.5

### Mac
- A click that moved even a pixel between pressing and letting go was lost, and had to be made two or three times. Piklin looked at which buttons were held when it handled the movement, not when it happened, so a mouse or a trackpad on a high-resolution screen, which slip a little, made buttons, albums, sidebar rows and photos ignore the click. It is fixed in the GTK that Piklin carries on a Mac

### Dark mode
- Piklin now follows the computer: dark when the computer is, light when it is not. The Theme setting offered "Match the computer", but the app stayed light unless it was changed by hand, and its colours were written for the light look only, so a dark computer made nothing dark
- A full dark look, made from the light one so that every screen has it, and so does every one added later: the library, the sidebar, the editor, the viewer, the settings and every dialog and menu. White becomes a deep grey and black a soft white; the photographs stay the brightest thing on the screen. The photo stage and the video controls stay dark in both looks, and the shadows and the dimming behind a dialog stay black
- What Piklin draws itself follows too: the camera at the top of the sidebar, the histogram and the curves of the editor, the grey blocks a photo shows while it loads, the box dragged to select photos. The map stays light in both looks
- Changing between the two takes effect at once, without closing Piklin

### Photo editor
- New icons in the editor, drawn for photo editing instead of the generic ones of the system: one for every tool, and for Back, Undo, Redo, Compare, Remove all edits and the three tabs
- **Copy Adjustments and Paste Adjustments.** Copy the edits of a photo from the editor, or from its right-click menu in the library, and choose what to copy: light and colour (the default), looks and effects, crop and rotate, portrait, text and frames, or local retouching. Right-click one photo or a whole selection and paste. The edits are added to what each photo already has, or replace the same tools if you choose Paste and Replace the Same Edits. Every photo keeps its own edits, the pasting happens in the background, and Undo in the message that follows puts every photo back as it was

### Videos
- A video that is already the smaller one is no longer made smaller a second time. A computer that had been waiting on a long list of videos made smaller again those the other computer had already finished, or ones that had been made smaller before: on one library more than half of the waiting list was work already done
- Two computers no longer start with the same video at the same time. Each takes the waiting videos in an order of its own, so the work divides between them instead of being done twice side by side

- Making videos smaller no longer starts until Piklin has read what the other computers left in the backup. It used to begin about twenty seconds after opening, before that was known, and redo work another computer had finished. Once it has read the backup, the waiting list is cleared of videos that are already the smaller one or that another computer has done
- The Pause and Resume button in Preferences is gone: making videos smaller looks after itself. A pause left by an earlier version is lifted

- The check that a smaller video is the same video as its original rejected good videos. It compared the frame at five moments of each, but took for each video the first frame from 50 ms before the moment, which is three frames apart when one video has a key frame exactly there and the other does not; in a scene with movement the same video no longer looked the same. A computer took the smaller video from the backup, found it different, threw it away and made the video smaller itself. The nearest frame to the moment is now taken in both
- The same check also rejected phone videos that are stored sideways and turned for showing, nearly all of those a phone records: the picture was made smaller by taking one pixel in six, and turned a quarter its grid starts a few pixels off, which made the same picture look different. It is now made smaller by averaging, and the frame is compared with the ones just before and after it, since two videos of the same thing do not put a frame at the very same instant. All the videos that had been rejected on one library now pass, and different videos still do not
- The same check took how long a video is from its frames divided by its frame rate, which is wrong for a screen recording, whose rate varies: it made one two percent longer than the identical one. It now takes the length the file states
- A computer whose OpenCV cannot decode AV1 (the Linux AppImage) took the videos another computer had made smaller from the backup, found them to be no video at all, and threw them away: the check that a smaller video is the same video always failed. It then made the videos smaller itself. The check, and the thumbnails of those videos, now read them with PyAV when OpenCV cannot

### Sidebar
- The numbers beside Favourites, Recently Deleted, the albums and the rest of the sidebar are gone. Recently Deleted shows its bin full - filled in, red and a little larger - when it holds something, and empty when it does not

- Two computers no longer undo each other's work in the backup. A computer that still held the large original of a video another computer had made smaller sent it to the backup over the smaller one, and the computer that had made it never noticed: it believed the smaller video was there. Now a large original that another computer has made smaller is not sent, and a computer that finds one of its smaller videos missing from the backup, or there at another size, sends it again

- However many computers share a backup, only one at a time makes videos smaller, and each reads first what the others did there: it takes a turn kept at the destination (made with a single call that exactly one of any number of computers wins, so two cannot both believe it is theirs), reads the record of the other computers, decides from it, makes one video smaller, says so, and lets the turn go. A computer that finds the turn held waits and asks again
- Each computer now keeps its own record, at the destination, of the videos it made smaller, and no other computer writes to it. Before, it was one file that any computer sent in its backup, and one that had an older copy wrote over what another had just said; the second computer then did the same video again. When two computers do make the same video smaller, the record keeps both versions as sizes of the one that replaces them, and each ends with the later
- The check for what another computer changed in the backup no longer stops at the top of it. Piklin looked at a few files at the top of the backup and, if they were as it had left them, read nothing more; a video overwritten inside a folder changes nothing at the top. It now always reads the other computers' records, says what it has made smaller, and looks, folder by folder and at most every ten minutes, that its own smaller videos are still in the backup
- A file with an "&" or a ";" in its name was listed at the backup under a cut-off name ("Mom &amp" for "Mom & Dad.mov"), so Piklin never found it there and sent it again at every backup. The names are now read as they are
- Tested with a simulation of many computers at once against a backup: 12 computers and 30 videos make 30 videos smaller, no more, in under 20 seconds; 30 computers and 40 videos the same; also when half of them join later with every large original, and when the backup has the large originals while one computer has the smaller ones and believes it sent them. In every case every computer and the backup end with the smaller videos

### NAS and backups
- Backing up to, restoring from and syncing through a NAS or a WebDAV server is faster. Piklin used to open a new connection for every file and send one file at a time; it now keeps the connection open and sends four files at once, up and down. What is saved on the NAS does not change, so what is already backed up stays as it is

## 2.0.4

### Videos: Original or Compressed
- Preferences › Storage has a new choice for the videos in your library. **Original** keeps every video exactly as your camera recorded it. **Compressed** makes them smaller in the background: nothing wider than 1080p, and a phone's 4K video can take a few percent of its size
- Compressed works on its own. It looks through the library, and again as new videos come in, and only while the computer is plugged in and not busy. The choice is yours to change at any time, and asks before it starts
- Each smaller video replaces the original only after Piklin checks it is the same video: its length, its sound and its picture. Albums, favourites and edits stay with it
- Your backup and your other computers follow. The big original stays in the backup for the days you keep old versions, then goes; every other computer fetches the smaller video, checks it against its own and replaces it
- Making a video smaller is faster: the AV1 encoder runs at a quicker setting for Compressed

### Fixes
- A computer that has not made a video smaller itself now takes the smaller one another computer already made from the backup, even when the backup has had no news since. It no longer makes that video smaller a second time
- Cutting a video that has sound could fail, and with it making that video smaller. It happened whenever the cut fell inside a block of the sound, which is most cuts of what a phone records (mono, 44.1 kHz). A video can now be cut anywhere

## 2.0.3

### Memory and processor
- Opening a large library no longer takes the processor and memory all at once: the first screen loads first, and the scan, the date fixes and the folder arrangement start a few seconds later, one after the other
- Each background task used to keep its own 64 MiB database cache; only the window keeps that now, the others use 4 MiB. With five tasks working, that is about 300 MB less
- Thumbnails and photo details are read in blocks of 256 instead of queueing thousands of jobs at once, and the memory they used is given back between blocks
- Photos shot upright and tagged as turned were read again every time Piklin opened. They are checked once and no longer read again

## 2.0.2

### Photo info
- New **Edit Info…** in the right-click menu of one photo or of a chosen group: title, caption, keywords, date and time, and a button to choose the place on the map. For a group, a field left empty keeps what each photo has, keywords can be added to the ones a photo already has or replace them, and a date can be set for them all or moved with their spacing kept. The photo files are not changed; what you type is kept in Piklin and in your backup

### Thumbnails
- The photos on screen get their thumbnails first. After scrolling past a few hundred photos, the ones you stopped on used to wait behind all the ones you passed

### The map and the menus
- Placing photos on the map: the picker's wheel no longer slides the map away when you zoom right in, and it has the same round zoom buttons as the main map
- A right-click menu fits in the window wherever you click, without a scrolling strip

### Backups to a NAS
- A restore or sync cut short no longer makes the next backup send everything again: the record of what came back is kept as the files arrive, and a file the NAS already has is known as sent
- When nothing is new, no backup is made at all
- Two computers sharing a NAS no longer back up to it at once: the second says who has the turn and waits

### Dates
- A photo with no date inside it was dated by its file, which after a restore or a copy is the moment it was written: photos sat under "Yesterday" among the ones from the night they were taken. Piklin now reads the date from the file name wherever it is in it (a Pixel burst cover, a screenshot, a WhatsApp image), and for a photo kept in a dated folder (Originals/2021/2021-10-17/) it uses that day. The file's own time still stands when it falls on the folder's day, and then it has the hour too
- Photos already in your library with a wrong date are corrected when Piklin opens. A date read from the photo or set by hand is never changed
- The time of a photo that carries none is now kept in the backup and brought back by a restore, so a restore cannot replace it by the day it ran

### The sidebar
- The heading over your albums and folders is now "Albums & Folders", since it holds both, and it no longer folds away: your albums are always one glance from the rest of the library. The folders inside it still open and close

### Linux
- The applications-menu launcher of the AppImage (and the .deb's) now names the window class Piklin really announces, so the desktop - GNOME, KDE, X11 - finds its icon

## 2.0.1

### The map
- Fixed the map showing a white screen with only the pins on it on Linux. The AppImage had no way to make secure connections for the map's tiles, and its Python did not know where a computer other than Ubuntu keeps its certificates, so nothing from the internet loaded: not the map, not place search, not updates. Both are fixed
- The ordinary map tiles now come from a source that allows apps, and if the map cannot be loaded at all Piklin says so instead of leaving it blank
- Zooming is gentler: the buttons move half a step, and the wheel and trackpad ease in and keep the place under the pointer, without running far ahead, so you do not lose where you are

- Zooming out now goes as far as it should: the clean map was stopping a level early and showed only a quarter of the world. The first view of your photos and the grouping of pins were off by the same level and are fixed too

### Restoring and syncing from a backup
- Folders and albums now appear first, as soon as a restore or a sync starts, laid out as they are in the backup, instead of after everything has come back. On a new computer the sidebar used to stay empty for as long as the photos took to arrive. If a sync was cut short, the folders and albums it had already brought are shown the next time Piklin opens
- It all comes in three steps: your folders and albums, then your photos, then your videos, which take the longest. The photos are shown as soon as they are in, while the videos are still coming, and the progress at the foot of the window says which is arriving
- A backup now sends photos before videos, in the same order

### Folders
- A folder no longer has to show the cover of its first album. Right-click a photo inside an album and choose Make Folder Cover to pick the picture for the folder the album is in. The choice is kept in your backup

### The AppImage
- The AppImage now offers to put Piklin in the applications menu, with its icon, the first time it opens. Nothing installs an AppImage, so until now it never had a menu entry or an icon and people made their own. It can be turned off again in Preferences, and the entry follows the file if you move it

## 2.0.0

### Your photos on a map
- The sidebar has a Map. It opens on everywhere you have been, as pins, and you travel through your library by place instead of by date. Photos taken close together are one pin, and the pins regroup as you zoom: a country, then a city, then a street corner. Each pin carries the number of albums behind it
- Resting on a pin slides its albums in beside the map - cover, name and how many photos are there - and the map stays where it is, so the next pin is one movement away. The panel waits until you choose; clicking an album opens it, and an arrow in the bar brings you back to the map exactly as you left it
- A button at the top left counts the countries your pins are in. Open it for the list, with the cities inside each country, and click a city to fly there

### Telling Piklin where you were
- Most photographs carry no coordinates. A camera without a receiver, a phone with location switched off, a picture that went through a messaging app - the place was never written, or was stripped on the way. A map built only on what cameras recorded stays nearly empty
- So you can say it yourself. Right-click an album - a holiday, a birthday, a week at the coast - type the country, the city and, if you like, the name of the place, and pick it from the answers. Every photo in that album lands on the map at once. Several albums can be placed together, and a chosen handful of photos can be given their own place when an album covers more than one
- Placing an album moves every photo in it, including any that already had coordinates, so nothing is left behind a few streets away
- Each album shows a dot once it is on the map: filled when it is settled in one place, faint while it is only partly placed or still scattered. With a hundred albums to go through, what is left is visible at a glance
- A place you set by hand is kept in your library and travels with your backup, and a later scan of the file never overwrites it

### Places named without asking anyone
- Piklin carries a list of the world's towns and reads it from your own disk, so turning a pin into "Miami" never sends a coordinate anywhere. With no connection the map still names its pins
- Searching for a place by name is the one thing that reaches the network, and only the words you type are sent - never a photograph, a filename or a coordinate of yours

### Clearer about making photos smaller
- Preferences › Storage now explains what making a photo smaller is, in plain words, before offering the choices: what it does, that Piklin tries each one on your own photos and measures the result rather than guessing, and that your folders and the photos already in your library are left alone
- Each choice says what it costs in room and what it costs in detail

### A quieter, clearer map
- The map is drawn in soft greys and whites, so the pins stand out, with streets, parks, neighbourhoods and places named in grey and no red crosses to be mistaken for a pin. Names come in the language Piklin is in. The background comes from OpenFreeMap; the ordinary map stays as the fallback

### Finding a place
- Searching asks two free map searches at once, shows each place once, and says what kind of place it is
- A link from Google Maps, Apple Maps or OpenStreetMap, or coordinates, pasted in the Place box become the place

### Name a group of photos
- Placing a selection lets you name the group. Photos placed under the same name stay together inside their album, under it as a title, and the name travels with your backup

### Search and sort your lists
- The albums in the sidebar, the albums at a pin, and the countries and cities on the map can be searched, and sorted A to Z, Z to A, or by how many photos they hold

### Export
- Any other folder is chosen in the system's folder chooser instead of typed

### Map zoom
- Pins no longer vanish or flicker when zooming: the map cannot be pulled back past the point where it repeats itself (that limit was never applied, so the world repeated and pins stayed only on the first copy), pins that stay the same are kept instead of redrawn, and the grouping is worked out once per quarter of a zoom step so panning never regroups them
- The wheel zooms on the place under the pointer and stops at the limits instead of sliding the map away; the zoom buttons go exactly one step

### Duplicates
- Find Duplicate Photos looks through every folder and reads every photo and video whole, so copies are found by their real content, in any folder, and files that only look alike are never merged. Progress is shown and it can be stopped

### Videos, much smaller
- Preferences › Storage can make the videos already in the library smaller with AV1 (SVT-AV1, free), trying gentler settings if a result would lose too much. Each new video is checked against the original before it replaces it, and the originals are kept for seven days

### Dragging photos out of Piklin
- Dragging photos to the desktop, a folder or another program now hands over the files. A photo that is kept as HEIC, WebP, AVIF, TIFF or a camera's RAW goes as an ordinary JPEG with its place and date, and one you edited goes as it looks now; a JPEG or PNG nobody changed goes exactly as it is. Dragging onto an album inside Piklin works as before

### Fixed
- A photo kept as WebP, AVIF, HEIC or TIFF, exported or saved smaller as a JPEG, lost where and when it was taken; the place, date and camera now stay
- A portrait photo exported or saved smaller could come out lying on its side, because it was turned twice; it now stays upright

### Smaller things
- The sidebar no longer counts the library on the interface's thread. On a large library the numbers used to hold everything up for half a second after every import, edit and backup; now the sidebar draws at once and the numbers arrive behind it
- No number beside All Photos or the albums: the footer already carries the library's count
- Piklin opens filling the screen
- The grey block that stands in for a thumbnail still being made was drawn transparent, so a grid waiting for its photos looked empty rather than busy

## 1.0.8

### Restoring brings your library back as it was
- A restore now leaves the library exactly as the backup had it. Folders that came back stayed missing from the sidebar, because the albums, folders and marks a restore brings back only reach the catalog when it is rebuilt, and until then Piklin wrote its emptier catalog back over the restored files
- Worse, those folders were taken as folders you had deleted, and the next backup carried that deletion to your other computers. Piklin no longer guesses a deletion from something missing: a folder or smart album you delete is written down the moment you delete it, the way albums already were
- A library waiting to be rebuilt after a restore leaves its restored files alone until the rebuild has run
- A restore that stops halfway - a full disk, a connection lost - still rebuilds on the next start, instead of leaving the library half restored and silent
- A restore that will not fit says so before it starts, naming what it needs and what the disk has, instead of filling the disk and stopping partway. Half a gigabyte is kept back for the thumbnails and the rebuilt catalog
- Rebuilding after a restore shows a window with a progress bar naming each step. It used to run before any window opened, so Piklin looked frozen for minutes on a large library, and force quitting it started the rebuild over

### The interface reads as it should on every Mac
- Piklin is drawn in the Inter it carries, on a Mac with fonts installed too. The typeface was registered through whichever fontconfig the Mac had, and inside Piklin.app there is none, so on a Mac with many fonts installed - a designer's Mac, with Adobe's fonts - the interface came out in heavy display faces that read as bold capitals. The app's own fontconfig now lists the typeface itself, and registering it falls back through every library Piklin carries

### Videos that take less room
- Preferences › Storage › Optimize Videos lists the videos in your library that can take less room, how much each would save, and how long this computer would need. Nothing runs until you say so
- A video is made smaller at a quality where the difference doesn't show at a normal viewing distance, and the smaller copy is kept only once it has been checked against the original: same length, sound still there, and the same picture at its start, middle and end. Albums, favourites, marks and edits stay with it
- Your originals are not thrown away the moment a video is converted. On a NAS or a drive each original waits five days in a folder of its own, "Converted Originals", and goes only after the smaller video has been checked again there and here. With a paid cloud backup only the smaller video is uploaded, so you don't pay for both, and the original waits five days on this computer. With no backup at all, Piklin says so plainly before starting and the original waits five days here
- The days count while Piklin runs, so nothing is let go behind your back: come back after a month away and the wait starts from the time it takes to check, not from a date on the calendar
- The work runs in the background, a video at a time and at low priority, only while the computer is plugged in and not busy, and it carries on where it stopped after Piklin is closed and opened again. The footer names the video it is on; it can be paused from the same place it was started
- Other computers on the same NAS replace their large copies with the smaller ones by themselves, each checking against its own original first

### Photo health
- Piklin looks after your photos' content. Now and then, a few at a time and at low priority, it reads each photo in full and compares it with what it recorded; a photo the disk damaged - same size, same date, different content - is put back from your backup, byte for byte and with its own date, and the damaged file is kept aside rather than thrown away. A whole library of 5,000 photos is read in under half a minute on an SSD, using about 30 MB of memory
- It never undoes a change you made: a photo another program rewrote, even keeping its size and date, is taken as changed on purpose. A photo whose backup copy is damaged too is left exactly as it is and listed in Backup, and photos outside the library, which have no copy in the backup, are reported rather than touched
- The check waits while a backup, a sync or a scan is reading the disk and while battery saver is on, and can be turned off in Backup › Photo Health

### Albums
- An album's name stays at the top while you scroll through its photos, so the back arrow to its folder is always one click away
- An album with photos and videos has a switch at the right of that bar - All, Photos, Videos - to see only one kind. Albums with only photos, or only videos, don't show it
- An album's count is the photos it shows. A photo moved to Recently Deleted, or hidden, stayed in the number, so deleting from an album never brought its count down; an album's cover is never a deleted photo either
- Dragging photos from an album onto another album moves them. They used to be added to the new album and left in the old one, so one count went up and the other never came down. Add to Album still keeps a photo in both

### Window
- A window made larger - maximized, or with a double click on its bar - goes back to the size it had. The photos' width had become the window's smallest width, so on Linux it could no longer shrink and jumped around the screen instead

### Clicks
- A click counts even when the pointer slips a little while pressing, as it often does on a trackpad or a high-resolution screen: buttons, album cards, sidebar rows and photos no longer need a second or third click, on Mac and Linux
- One click chooses a photo as soon as it is pressed

### Memory
- Piklin stays light however much you browse. Scrolling through 1,500 photos used to add about 950 MB; now it adds about 20 MB, and Piklin rests at about 270 MB on a Mac instead of more than a gigabyte
- Rows of photos far from the screen let go of their thumbnails, and photos that leave the screen let go of their pictures. A thumbnail no longer on screen is read again from the thumbnail folder when you come back to it, in milliseconds; only about a screen of them is kept ready, 16 to 32 MB
- Thumbnails are decoded at the size they are shown, in the grid, Years and Months, folders and the sidebar: an album cover in the sidebar is 22 points, not a 400-pixel picture
- On Linux, the threads that make thumbnails, scan and back up no longer each keep a pool of memory after their work is done: with a library of 5,000 photos, Piklin went from 782 MB to 312 MB once it had settled
- The graphics card is asked to let go of pictures that left the screen after 3 seconds instead of 15, and memory Piklin is done with goes back to the computer after thumbnails are made

### Syncing between computers
- Piklin knows what changed without reading the whole backup. Every change on any computer sends a new catalog, so looking at the handful of files at the top of the NAS or cloud folder is enough to tell whether another computer sent something; only then is the backup read file by file. Opening Piklin and the check every ten minutes no longer walk thousands of files on the NAS
- A backup sends what changed using what this computer already knows it sent, instead of listing the whole destination first. The catalog goes last, so a backup cut short is picked up again. Once every six hours the whole backup is read anyway, in case files there were changed by hand

### Light on computers with little memory
- On a computer with 8 GB of memory or less, Piklin makes two thumbnails and reads one video at a time in the background, and a few more with 16 GB: it takes longer, and the computer keeps answering
- Videos are measured with one decoding thread: measuring a library's videos made Piklin's memory jump by 600 MB on an eight-core laptop
- The one-time check of video sizes really happens once: its note was dropped when Piklin closed, and every video was measured again each time Piklin opened

### First steps
- The first time Piklin opens, a short guide asks one thing at a time, in five numbered steps: the language, where the library lives, where your photos are - or a backup to restore from - and where to keep a copy. The guide changes language as soon as you choose one, and nothing happens until the last step, so Back can change any answer

### Look and feel
- Edit, in the photo viewer, is a black pill with an icon
- Alerts show their answers as rounded buttons, the suggested one in black
- Animations on buttons and cards change only what they need to: Piklin could close on a Mac while photos were switched quickly
- Tooltips are readable everywhere: over the photo viewer's buttons they were black text on black
- A hairline of space between photos in the grid, and the same margin at both sides of the photos

## 1.0.7

### All your computers, in step
- Computers that back up to the same NAS or cloud folder keep each other up to date: new photos and videos, albums, folders, Smart Albums, favourites, marks and edits made on one appear on the others, on Mac and Linux alike. Changes are brought in before every automatic backup and every ten minutes while Piklin is open
- When two computers change the same thing, the latest change wins: a photo added back to an album after it was removed stays, a folder deleted on one computer is deleted on the others unless it changed afterwards, and a cleared favourite stays cleared

### Backups
- Cloud backups need nothing else installed: Piklin carries rclone. Choose Google Drive, OneDrive, Dropbox, pCloud or Box and sign in once, in your browser
- Encrypted backups: Encrypt This Backup encrypts your photos and videos on your computer before they leave it, so the NAS or cloud service only holds unreadable data. A recovery key, shown once to write down, opens the backup on another computer. Once the encrypted backup is complete, Delete Unencrypted Copy frees the space of the copy made before
- The access a cloud service grants Piklin is kept in an encrypted file, with its key in the password manager
- A NAS reached over plain http:// is marked as not encrypted, and Use a Secure Connection finds its https:// address and asks to confirm its certificate
- A restore brings back your folders, favourites and Smart Albums even when Piklin had already written its own empty lists, and the rebuild after it works when the library is inside a watched Pictures folder. A rebuild that fails is tried again on the next start
- A new or empty library connected to an existing backup never replaces the backup's folders or favourites with blank lists, and albums deleted on another computer don't come back with a restore

### Photos and albums
- A double click always opens a photo, on its second press: before, the slightest movement while clicking started a drag, and a photo could take many clicks to open
- One click frames a photo, as in Photos on a Mac. Clicking it again keeps it chosen, and the check marks show only while several photos are chosen
- Albums, Smart Albums and folders can be moved to any folder, or back to the top: right-click and choose Move To…, or drag them onto a folder in the sidebar, or onto the Albums heading to take them out of every folder. Several albums chosen inside a folder move together. A folder is never moved inside itself, and backups keep where everything was moved

### Light on any computer
- Thumbnails, reading photos, imports and compression, video, exports and backups run at low priority, with only as many at once as the computer can spare: a core stays free, and each large photo gets its share of memory. Older computers stay responsive, and the processor doesn't race on a battery

### Mac
- Dragging photos onto an album, or an album onto a folder, no longer freezes Piklin on macOS 26: the app carries GTK 4.20.4
- Close, minimise and zoom sit in the top-left corner, where a Mac puts them, and answer the first click
- Reopen Now, after a restore, reopens Piklin

### Updates
- Update Available never offers an older build of the same version

## 1.0.5

### Piklin on the Mac
- Piklin runs on Mac: one download for Apple Silicon and Intel Macs with macOS 14 or newer, a disk image to drag into Applications. It is the same Piklin as on Linux - the same library, editor, video tools, backups and languages
- The window looks at home on a Mac: close, minimise and zoom at the top left, the Piklin name at the top of the sidebar, and dialogs sized to fit smaller screens
- Keyboard shortcuts use ⌘ on a Mac (⌘E to export, ⌘R to rotate), ⌘-click adds to a selection, and the delete key moves photos to Recently Deleted
- Backup passwords are kept in the Mac's Keychain
- Piklin follows the Mac's language, as it follows the computer's language on Linux
- USB drives and memory cards appear in the sidebar when they are connected
- Updates work on a Mac as on Linux: Piklin asks first, then installs only versions signed by Vezzu Studio

### iPhones, iPads and cameras
- Connect an iPhone or iPad and its photos appear under Devices, ready to browse and import, on Mac and on Linux. Unlock it and choose Trust when it asks
- Cameras and Android phones set to "Transfer photos (PTP)" work on a Mac as they do on Linux
- An iPhone shows once in the sidebar, not once for each way Linux can reach it
- With an iPhone connected on Linux, the rest of the library keeps its photos: the phone's thumbnails, read slowly over USB, no longer hold up every other thumbnail and leave the photos blank until the phone is unplugged
- An iPhone on Linux is read to the end instead of staying on "Reading iPhone…"
- Dropping photos on an album adds the photos it can, instead of failing and adding none

### Linux
- Piklin also comes as an AppImage, for amd64 and arm64: one file to download, make executable and open, nothing to install. It runs on Ubuntu 24.04, Linux Mint 22, Debian 13, Fedora 40 and newer, and updates itself like the .deb, asking first and accepting only versions signed by Vezzu Studio
- Piklin is available for 64-bit ARM computers as well (arm64), such as a Raspberry Pi 5 or an ARM laptop running Ubuntu 24.04 or newer
- Video always plays with sound: the package now asks for the small library miniaudio needs, which some minimal installations lacked

### Everywhere
- Restore onto any computer: a backup restored on another computer - a Mac, Linux, another user name - brings back your albums, their folders, favourites and edits, not only the photos. A restore also goes on when one file can't be put back
- A video is never refused when the image library cannot open it: Piklin reads it through its own video engine instead

## 1.0.4

### Languages and help
- Piklin speaks Spanish as well as English. It follows the computer's language, or choose one on the welcome screen or in Preferences › General
- How to Use Piklin, under Help in the menu (or F1), explains in plain words what each button does

### Albums and photos
- Selecting a folder in the sidebar shows its albums and folders as cards; click one to open it
- Rotating is instant: the photo turns in the grid straight away, even with many selected. Rotate buttons are now in the bar for chosen photos too
- Photos of the same day run on without a line or gap, and days are separated only by space and their date
- Edited photos show a green mark on a white disc
- Crop works as expected: the whole photo shows while you set the crop, dragging inside draws a new one, corners and edges can be pulled, and Square, 16:9 or Original reshape the rectangle at once. Before, each adjustment cropped the crop again and the rectangle jumped
- Brush, Healing and Selective start clean: strokes and points painted in one layer no longer appear in the next one, on any photo
- Edits are kept: moving a slider in the editor is saved as you go, and leaving the editor by any way - Back, Esc or Return - saves the last change. Before, a tool could be kept with all its values at zero
- Drag the edge of the sidebar to make it wider, up to 420 pixels, so the names of albums and folders deep inside other folders can be read; Piklin remembers the width
- Clicking a photo while the photos are still gliding after a touchpad flick stops them at once, so the photo you click stays under the pointer instead of sliding away
- Choosing a photo keeps the photos where they are. Before, far down a big library the view jumped away as soon as one was clicked: the list took the photo's whole row as focused and scrolled it into view, and a row is taller than the window, so it was pulled up or centred. The activity log found it. Also, the count of chosen photos, shown at the top, narrowed the sidebar and resized every photo; it is now in the bar at the bottom, that bar lies over the photos, and photos changing size keep your place
- Double-clicking a photo right after clicking it opens it, instead of only showing the selection bar
- The Left and Right arrow keys move between photos in the viewer
- Choose the photo shown for an album: right-click it inside the album and choose Make Album Cover
- An album shows its name and number of photos above its photos, with a soft grey back arrow on the same line as the name when the album is inside a folder; a folder inside another folder has one too
- Each album in the sidebar shows a small picture of its cover
- Right-click on empty space - in an album, in a folder or in any view - to make a New Album or New Folder right there, without going to the sidebar
- Drag from empty space to draw a rectangle that chooses the photos it touches, or the album cards in a folder; Ctrl or Shift adds to what is chosen, and a click on empty space lets go. Chosen albums can be deleted together from their right-click menu
- Counts say what they count: "12 videos" for videos, "3 photos and 2 videos" for both, in album headings, days and folder cards
- Videos to be made smaller are copied as they are first, so an import no longer sits on one percentage while a long video converts; they are made smaller afterwards in the background, one at a time, with their progress under the sidebar, and carry on the next time Piklin opens. A video is only replaced when the smaller copy saves at least a tenth, and it keeps its albums, favourite and edits
- Videos filmed upright on a phone play upright, in the viewer and when previewed in the grid, and their size is recorded the right way round
- Photos and videos dragged into Piklin are stored at the size chosen in Preferences › Storage, like those from a camera or a USB drive
- Folders and albums share one A to Z order, instead of all folders first
- Albums and folders are listed the way people count: "Birthday 2" comes before "Birthday 10", in the sidebar, in folders and in the Add to Album list
- Duplicates shows each set of identical copies as its own group, says at the top what the button does, and the button says exactly what goes: Keep One of Each, or Remove 2 Extra Copies for the groups you select
- One click on a folder in the sidebar shows its albums on screen without opening it in the sidebar; a double click opens or closes it there
- Albums and views open at once, however many photos they hold: the first photos appear in a few hundredths of a second, the rest load in the background, only the photos near the screen are drawn, and going back to a view shows its thumbnails straight away

### Backups
- Backups go in a Piklin folder inside the folder you choose, made when it is missing. A backup already made straight in the chosen folder is moved into it on the destination itself, without sending anything again; nothing else there is touched
- A backup folder shared with other files - such as a NAS photo share with a Lightroom catalog in it - is no longer searched through: Piklin reads only its own part, so a backup that stayed on "Connecting…" for many minutes now starts straight away, and a restore never brings those other files into the library
- Automatic backup no longer stops with "No password saved" when Piklin opens right after logging in, before the password keyring is unlocked: it waits and tries again a few minutes later
- While Piklin checks what is already backed up, it says so
- The backup status shows how far along it is while a big video uploads, instead of staying on the same file number for minutes
- When one destination can't be reached - a NAS at home while you travel - the others are still backed up, and the one that was missed catches up by itself once it can be reached again

### Updates
- Every time Piklin opens it looks for a new version, and every hour while it stays open
- When there is one, Piklin asks: a window shows the version you have next to the one you would get, what is new in a few words, and Install Now or Cancel. Nothing is installed without asking - some people prefer the version they have
- A black Update Available button with a ringing bell stays above Support on Ko-fi while an update is waiting, and opens the same window
- Installing happens inside that window, without a password and with only packages signed by Vezzu Studio; Piklin then reopens in the new version once nothing is copying and no editor is open
- After reopening from an update, the taskbar shows Piklin with its logo, instead of "app.py" with no icon
- Turn checking for updates off in Preferences › General
- A version published again with fixes, under the same number, is installed too: Piklin compares the build and still installs only packages signed by Vezzu Studio

### Activity log
- Help › Activity Log shows what Piklin noted - backups, updates, errors, and the photos moving just after a click - to find the cause of a problem. It stays on this computer and is never sent anywhere, holds no passwords, addresses or photo names, and never grows past about 1.6 MB. Copy it and paste it into a problem report on GitHub with Report a Problem

### License
- Piklin is free to use for any purpose, at home or for work, including commercial use. Modifying, redistributing or selling it is not permitted. Your photos and everything you make with Piklin are yours

## 1.0.3

### Importing
- Photos copy several at a time, and appear in the library - and in the album you dropped them on - as they arrive, instead of all at the end
- A card at the bottom right shows what is copying, how far along it is, and a Stop button; stopping keeps everything already copied, in its album
- Photos dragged in from the desktop, a folder or a USB drive are copied as they are, which is much faster; cameras and memory cards still follow the "Make imported photos smaller" setting
- Closing Piklin while photos are copying asks first
- USB drives open straight away and show their photos grouped by folder, loading more as they are found

### Editing
- Rotate a photo or video in one click: Rotate Left and Rotate Right in the photo's toolbar and in the right-click menu, or Ctrl+R and Ctrl+Shift+R. Works on several selected photos at once
- Thumbnails show your edits: a rotated, cropped or adjusted photo looks the same in the grid as in the viewer
- Look for New Photos moved to F5

### Updates
- Piklin tells you when a new version is available, with a link to download it. Check any time with Check for Updates… in the menu, or turn the daily check off in Preferences

## 1.0.2

Fixes for 1.0.1.

- USB sticks and external drives now appear under Devices, even when their photos are not in a camera folder, so you can browse them and import what you choose
- Photos and videos dragged from the desktop or a file manager onto an album, or onto the photo area, are now copied into the library, with progress shown while they copy. Folders can be dropped too
- Notices such as “Imported 3 items” are a white pill with black text, matching the rest of the app

## 1.0.1

### Automatic backup

- Connect a folder, a NAS, a WebDAV server or a cloud service once, and new photos, videos and edits are copied there automatically, a minute after they change
- Nothing runs when nothing changed: no background checks and no network use, which is easy on laptop batteries. Backups also wait while battery saver is on
- When the destination can't be reached, Piklin waits for the connection to come back and tries again
- The sidebar shows the backup status, such as "Backed up 2 minutes ago"
- Turn automatic backup on or off in Preferences › Backup
- On first launch, Piklin asks where to keep a copy of your photos
- When a file changes, its older copy stays on the destination in a `.piklin-versions` folder for 7, 30 or 90 days, so an earlier edit can still be recovered

### Library

- Albums sit at the bottom of the sidebar, under a dividing line, so a growing list of albums never pushes the other sections down
- The sidebar scrolls when its contents are taller than the window

### Backup

- **Restore Missing Files** copies back from a backup only what is gone from the library. Nothing in the library is replaced, and photos deleted on purpose stay deleted. Restoring into a new, empty library brings back albums, favourites and edits too
- Connect to a WebDAV server on your home network with `http://`; servers on the internet still require `https://`
- Trust a server's own security certificate, as most home NAS devices use, after checking its fingerprint. Piklin warns if the certificate ever changes
- A file already backed up is never uploaded again unless it was modified, including on servers that stamp files with their upload time
- The backup folder is created on the server when it does not exist yet
- Backups stop with a clear message when the password is wrong, the server cannot be reached or the address does not answer WebDAV
- Favourites, hidden photos and removed photos are now included in backups, with a consistent copy of the library index
- Large videos are uploaded without loading them into memory
- Backup destinations can be edited, including their password
- Choose the backup folder from a list of the folders on your NAS, WebDAV server or rclone cloud, instead of typing its path
- Works with servers that do not allow listing a whole folder tree at once, such as QNAP

## 1.0.0

The first public release of Piklin.

### Library

- One library package, `Piklin Library.piklin`, that can be moved or copied to another disk and opened from its new location
- Browse by Years, Months, Days or All Photos, with filters, search, favourites and hidden items
- Albums, folders and smart albums with rules such as camera, date, media type or video length
- Import from cameras and memory cards, including dragging straight onto an album; photos can be stored smaller without visible loss
- Duplicate finder, Recently Deleted (30 days) and an Imports view
- Photos are renamed on disk, together with their edits and album entries

### Editing

- 28 non-destructive editing tools and Looks; the original file is never changed
- Export as JPEG, WebP, AVIF or PNG, choosing the size and whether to include location and metadata

### Video

- Plays every common format with sound, at speeds from 0.25× to 2×, frame by frame
- Silent previews when the pointer rests on a video in the grid
- Trim the ends, cut out pieces from the middle, rotate, flip, crop, change speed and mute
- Save any frame as a full-resolution photo
- Export as MP4 (H.264), WebM (VP9) or animated GIF, from 480p to 4K
- Live photos: a still and its short clip appear as one item

### Backup

- Back up the library to the destination you prefer: any mounted folder (NAS, USB drive, cloud drive), a WebDAV server (Nextcloud, ownCloud, QNAP, Synology, pCloud, Box) or more than seventy services through rclone
- Passwords are kept in the system keyring; backups never delete your photos

### Privacy

- No accounts, analytics or telemetry; nothing leaves the computer unless you set up a backup
