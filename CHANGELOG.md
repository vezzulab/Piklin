# Changelog

## 1.0.4

### Languages and help
- Piklin speaks Spanish as well as English. It follows the computer's language, or choose one on the welcome screen or in Preferences › General
- How to Use Piklin, under Help in the menu (or F1), explains in plain words what each button does

### Albums and photos
- Selecting a folder in the sidebar shows its albums and folders as cards; click one to open it
- Rotating is instant: the photo turns in the grid straight away, even with many selected. Rotate buttons are now in the bar for chosen photos too
- Photos of the same day run on without a line or gap, and days are separated only by space and their date
- Edited photos show a green mark on a white disc
- Edits are kept: moving a slider in the editor is saved as you go, and leaving the editor by any way - Back, Esc or Return - saves the last change. Before, a tool could be kept with all its values at zero
- Double-clicking a photo right after clicking it opens it, instead of only showing the selection bar
- The Left and Right arrow keys move between photos in the viewer
- Choose the photo shown for an album: right-click it inside the album and choose Make Album Cover
- An album shows its name and number of photos above its photos
- Albums and folders are listed the way people count: "Birthday 2" comes before "Birthday 10", in the sidebar, in folders and in the Add to Album list
- Duplicates shows each set of identical copies as its own group, says at the top what the button does, and the button says exactly what goes: Keep One of Each, or Remove 2 Extra Copies for the groups you select
- One click on a folder in the sidebar shows its albums on screen without opening it in the sidebar; a double click opens or closes it there
- Albums and views open at once, however many photos they hold: the first photos appear in a few hundredths of a second, the rest load in the background, only the photos near the screen are drawn, and going back to a view shows its thumbnails straight away

### Backups
- Backups go in a Piklin folder inside the folder you choose, made when it is missing. A backup already made straight in the chosen folder is moved into it on the destination itself, without sending anything again; nothing else there is touched
- A backup folder shared with other files - such as a NAS photo share with a Lightroom catalog in it - is no longer searched through: Piklin reads only its own part, so a backup that stayed on "Connecting…" for many minutes now starts straight away, and a restore never brings those other files into the library
- While Piklin checks what is already backed up, it says so
- The backup status shows how far along it is while a big video uploads, instead of staying on the same file number for minutes
- When one destination can't be reached - a NAS at home while you travel - the others are still backed up, and the one that was missed catches up by itself once it can be reached again

### Updates
- Piklin updates itself: when a new version is out it says so, installs it and reopens in it, without a password. It waits until nothing is copying and no editor is open. Only packages signed by Vezzu Studio are installed. Turn it off in Preferences › General

- A version published again with fixes, under the same number, is installed too: Piklin compares the build and still installs only packages signed by Vezzu Studio

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
