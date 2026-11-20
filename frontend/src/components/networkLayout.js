// Place well-connected nodes near the centre, then let links pull neighbours together.
export function networkLayout(nodes, edges) {
  const points = new Map(nodes.map(({ id, title }) => [id, {
    id, degree: 0, labelWidth: Math.min(174, title.length * 6.5), x: 0, y: 0,
  }]))
  const links = edges.flatMap(({ source, target }) => {
    const a = points.get(source), b = points.get(target)
    if (!a || !b || a === b) return []
    a.degree += 1
    b.degree += 1
    return [[a, b]]
  })
  const ordered = [...points.values()].sort((a, b) => b.degree - a.degree || a.id.localeCompare(b.id))
  const maxDegree = ordered[0]?.degree || 1

  ordered.forEach((point, index) => {
    const angle = index * Math.PI * (3 - Math.sqrt(5))
    point.x = 103 * Math.sqrt(index) * Math.cos(angle)
    point.y = 73 * Math.sqrt(index) * Math.sin(angle)
    point.diameter = 15 + 35 * (point.degree / maxDegree) ** .75
  })

  // Bound the work on large maps. The initial placement also works for disconnected nodes.
  const passes = Math.min(135, Math.floor(4500000 / Math.max(1, ordered.length ** 2)))
  for (let step = 0; step < passes; step += 1) {
    const cooling = 1 - step / passes * .85
    for (const point of ordered) {
      point.dx = -point.x * .007
      point.dy = -point.y * .007
    }
    for (let i = 0; i < ordered.length; i += 1) {
      const a = ordered[i]
      for (let j = i + 1; j < ordered.length; j += 1) {
        const b = ordered[j]
        const x = b.x - a.x, y = b.y - a.y
        if (Math.abs(x) > 310 || Math.abs(y) > 230) continue
        const distance = Math.hypot(x, y) || 1
        const width = (a.labelWidth + b.labelWidth) / 2 + 16
        const height = (a.diameter + b.diameter) / 2 + 27
        const separation = Math.max(0, 1 - Math.hypot(x / width, y / height))
        const push = 7100 / (distance ** 2 + 140) + separation * 34
        a.dx -= x / distance * push
        b.dx += x / distance * push
        a.dy -= y / distance * push
        b.dy += y / distance * push
      }
    }
    for (const [a, b] of links) {
      const x = b.x - a.x, y = b.y - a.y
      const distance = Math.hypot(x, y) || 1
      const pull = (distance - 120) * .031 / Math.sqrt(Math.max(a.degree, b.degree))
      a.dx += x / distance * pull
      b.dx -= x / distance * pull
      a.dy += y / distance * pull
      b.dy -= y / distance * pull
    }
    for (const point of ordered) {
      point.x += Math.max(-16, Math.min(16, point.dx)) * cooling
      point.y += Math.max(-16, Math.min(16, point.dy)) * cooling
    }
  }
  return points
}
