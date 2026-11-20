// The two icons of the view switch, in the same stroke style as the shared icon set (components/ui.jsx).
const Glyph = ({ children, size = 18 }) => (
  <svg className="ico" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
)

export const SceneIcon = (props) => <Glyph {...props}><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" /><path d="M12 12l8-4.5M12 12L4 7.5M12 12v9" /></Glyph>
export const PlanIcon = (props) => <Glyph {...props}><rect x="3" y="4" width="7" height="6" rx="1.2" /><rect x="14" y="14" width="7" height="6" rx="1.2" /><path d="M10 7h4a3 3 0 0 1 3 3v4" /></Glyph>
