// The few drawing icons the editor adds to the shared set, in the same stroke style (components/ui.jsx).
const Draw = ({ children, size = 18 }) => (
  <svg className="ico" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
)

export const EditIcon = {
  undo: (p) => <Draw {...p}><path d="M9 14L4 9l5-5" /><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" /></Draw>,
  redo: (p) => <Draw {...p}><path d="M15 14l5-5-5-5" /><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13" /></Draw>,
  grid: (p) => <Draw {...p}><path d="M4 4h16v16H4zM4 12h16M12 4v16" /></Draw>,
  keyboard: (p) => <Draw {...p}><rect x="2.5" y="6" width="19" height="12" rx="2" /><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10" /></Draw>,
  history: (p) => <Draw {...p}><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5M12 7v5l3 2" /></Draw>,
  group: (p) => <Draw {...p}><rect x="3" y="3" width="18" height="18" rx="2" strokeDasharray="3 2.5" /><rect x="8" y="8" width="8" height="8" rx="1.5" /></Draw>,
  link: (p) => <Draw {...p}><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" /><path d="M14 10a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 11 18.7l1-1" /></Draw>,
  trash: (p) => <Draw {...p}><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></Draw>,
  alignLeft: (p) => <Draw {...p}><path d="M4 3v18" /><rect x="7" y="5" width="12" height="5" rx="1" /><rect x="7" y="14" width="7" height="5" rx="1" /></Draw>,
  alignCenter: (p) => <Draw {...p}><path d="M12 3v18" /><rect x="5" y="5" width="14" height="5" rx="1" /><rect x="8" y="14" width="8" height="5" rx="1" /></Draw>,
  alignRight: (p) => <Draw {...p}><path d="M20 3v18" /><rect x="5" y="5" width="12" height="5" rx="1" /><rect x="10" y="14" width="7" height="5" rx="1" /></Draw>,
  alignTop: (p) => <Draw {...p}><path d="M3 4h18" /><rect x="5" y="7" width="5" height="12" rx="1" /><rect x="14" y="7" width="5" height="7" rx="1" /></Draw>,
  alignMiddle: (p) => <Draw {...p}><path d="M3 12h18" /><rect x="5" y="5" width="5" height="14" rx="1" /><rect x="14" y="8" width="5" height="8" rx="1" /></Draw>,
  alignBottom: (p) => <Draw {...p}><path d="M3 20h18" /><rect x="5" y="5" width="5" height="12" rx="1" /><rect x="14" y="10" width="5" height="7" rx="1" /></Draw>,
  spreadAcross: (p) => <Draw {...p}><path d="M4 4v16M20 4v16" /><rect x="9" y="7" width="6" height="10" rx="1" /></Draw>,
  spreadDown: (p) => <Draw {...p}><path d="M4 4h16M4 20h16" /><rect x="7" y="9" width="10" height="6" rx="1" /></Draw>,
}
