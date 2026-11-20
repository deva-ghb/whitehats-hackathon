/** Plain clicks move this tab. A modified click or the middle button opens a new tab.
 *  A right-click does nothing here, so the browser menu can open.
 *  Clicks on a real link, button, or field are left to that control. */

export function followRow(event, href, navigate) {
  if (event.target.closest('a, button, input, textarea, select, label')) return
  if (event.button === 2) return
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button === 1) {
    event.preventDefault()
    window.open(href, '_blank', 'noopener')
    return
  }
  navigate(href)
}
