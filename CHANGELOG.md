# Changelog

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
- Piklin is available for 64-bit ARM computers as well (arm64), such as a Raspberry Pi 5 or an ARM laptop running Ubuntu 24.04 or newer
- Video always plays with sound: the package now asks for the small library miniaudio needs, which some minimal installations lacked

### Everywhere
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
