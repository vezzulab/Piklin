# Changelog

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
