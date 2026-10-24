# Contributing to Piklin

Thank you for helping make Piklin better.

## Reporting a bug

Please [open an issue](https://github.com/vezzulab/Piklin/issues/new/choose) using the **Bug report** form. The most helpful reports include:

- your distribution and version, for example Linux Mint 22.1
- the Piklin version, shown in **About Piklin**
- the steps that make the problem happen
- any message printed when you start Piklin from a terminal with `piklin`

Please don't attach photos or videos you wouldn't want to be public.

## Suggesting a feature

Use the **Feature request** form. Tell us what you are trying to do; the reason often matters more than the exact feature.

## Pull requests

Piklin is source-available under the [Piklin License](LICENSE), which does not permit modified versions. We therefore can't accept pull requests. Bug reports and ideas are always welcome, and we read every one.

## Dialogs and alerts

Every modal in Piklin follows one formula, so none needs fixing again:

- **One answer does the thing** (Create, Rename, Delete, Leave, Join). It is the black pill: `SUGGESTED`, or `DESTRUCTIVE` when it deletes. There is never more than one.
- **Every other answer is a soft grey pill**, with air between the pills. The way out is "Cancel" (or "Keep Working"). It closes the alert.
- **A short heading and one or two plain sentences.** What the person fills in goes in the extra child, under the text.

Build a new alert with `piklin.ui.modal.alert()`; it applies the formula. The look is written once, in the "alerts" block of `piklin/ui/style.css`, and covers every `Adw.AlertDialog`: change it there, not in one dialog. `tests_full.py` ("Modals: one formula") fails if an alert with several answers leaves all of them grey, or if the style loses the spacing or the black pill.

## Security

Please don't report security problems in public issues. See [SECURITY.md](SECURITY.md).
