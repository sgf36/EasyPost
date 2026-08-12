# Easy-Post Desktop — release notes 1.2.1

Master English ("what's new") copy for the version 1.2.1 store listings. House
style: no Oxford commas, no abbreviations, no pronouns, British spelling.

Applies to the Microsoft Store listing (ReleaseNotes field) and, reworded per
platform limits, the App Store "What's New". Version 1.2.1 is a security fix
with one usability change alongside it. No screenshot changes: the only page
that moved is Settings, and Settings is never photographed for the listing.

Written for a shipper, not a developer. The plain statement — the keys are no
longer put on screen — is more useful than the mechanism, but it has to be said
outright rather than softened, because anyone who ran 1.2.0 in front of a camera
needs to know to rotate the key.

## en-US ReleaseNotes

The Microsoft Store ReleaseNotes field caps at **1500 characters**, and every
translation has to fit the same cap. The copy below is 1036, leaving 464 of
headroom — unlike 1.2.0, which had to be cut from 1634. Translations may run
longer than the English without trouble, but check each one before importing.

• Stored API keys are no longer shown on the Settings page. Earlier versions filled both key boxes with the saved keys every time the page was opened, and the Show keys button then displayed the production key in full, visible to anyone looking at the screen and captured by any screenshot, screen share or recording. The page now shows only whether a key is saved.

• An empty key box now means leave that key as it is. The Settings page can be opened and saved for any other reason without disturbing the saved keys, and neither key has to be pasted again to change an unrelated setting.

• Removing a saved key is now a deliberate step. A new Forget stored keys button clears both keys from this computer, after asking for confirmation.

• Printer type and label calibration have moved to Settings. The printer on the desk, and the fine adjustment across and down the page, are now set alongside the label format and size instead of only inside the Export print sheet dialog, which could not be reached until a label had been bought.

## Translations

All 47 listing languages are in `release-notes-1.2.1-translations.json`, which is
the file `build_listing_import.py` reads by default.

Every translation names the Settings page and the Forget-stored-keys button with
the exact string the app shows in that language, taken from
`app/resources/locales/`, so the note and the interface agree rather than
offering the reader two different names for the same button. Checked: 47 of 47
carry the button label verbatim.

None is near the 1500-character cap — the longest is French at 1254, the
shortest Chinese at 319, because the languages that pack the most meaning into
the fewest characters do exactly that.

## Sequencing

These notes describe a version the Store does not serve until the 1.2.1 package
is submitted. Partner Center ships listing changes and packages in the *same*
submission, so add `EasyPostDesktop-1.2.1.0.msix` to the submission that carries
these notes. Publishing 1.2.1 notes against the 1.2.0 package would describe
features nobody could download yet.
