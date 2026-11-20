# Select

**Status:** accepted
**Date:** 2026-10-22

The dropdowns use Radix Select (`@radix-ui/react-select` 2.4.1). Headless UI Listbox was the other candidate.

Radix Select already implements the listbox keyboard pattern this screen needs: arrow keys, Home, End, type-ahead, Enter and Escape, plus groups and a visible tick on the selected option. Headless UI Listbox would have meant writing that keyboard behaviour here.

Neither library opens a bottom sheet or filters a long list. Those two behaviours live in `components/ui.jsx` and use only the tokens in `styles.css`. A search field appears when a list has more than 7 options. No list in the product is that long today, so the field stays hidden until one is. Regions are grouped by geography anyway.

Disabled options can carry a sentence that says why. No current choice is disabled.

Arrow keys, Home, End and type-ahead move the highlight. Enter commits that option. Escape closes the list without a change. The browser test does both with the keyboard and then reloads the saved plan.
