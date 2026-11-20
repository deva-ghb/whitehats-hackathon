// Sense points for the scene. A sense point is {id, node, status, mode, reason}: status is accepted, suggested or rejected.
//
// The scene draws whatever list it is handed. The Studio hands it the stored list (useSensePoints.js: the server keeps them per
// clone version, from the analysis, a person's decisions and hand placement).

export const SENSE_STATUS = ['accepted', 'suggested', 'rejected']

/**
 * The state tint each node wears: 'watched' for a node with an accepted sense point, 'finding' for one with an open finding
 * (a finding wins, since it is the more urgent thing to see). `findings` maps a node id to the text of what was found.
 */
export function statesFor(points = [], findings = new Map()) {
  const states = new Map()
  for (const point of points) if (point.status === 'accepted') states.set(point.node, 'watched')
  for (const id of findings.keys()) states.set(id, 'finding')
  return states
}

/** The words for a screen reader and the tooltip. */
export const senseWords = (status) => (status === 'accepted' ? 'Sense point, accepted'
  : status === 'rejected' ? 'Sense point, rejected' : 'Sense point, suggested, waiting for a person to accept it')
