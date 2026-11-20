// GL.1: the platform's own words, explained once. Every screen that shows one of these words explains it from here, so the
// wording never drifts between screens. Plain words only: the reader may never have used WhiteHats before.

export const GLOSSARY = {
  whitehat: 'A WhiteHat is a specialist that watches one area, such as sign-ins or cloud changes, and raises a signal when it sees something worth a look.',
  active: 'Active: reads its data and raises signals. High and critical ones alert your team.',
  learning: "Learning: reads its data and keeps what it would have raised, but alerts no one, can't be put on an incident and doesn't change the security score. Use it to try a WhiteHat without noise.",
  dark: 'Dark: switched off. It reads nothing, raises nothing, and counts as a blind spot.',
  mark_active: 'Switch to Active: it reads its data and raises signals, and high and critical ones alert your team.',
  mark_learning: "Switch to Learning: it keeps reading and records what it would have raised, but alerts no one and doesn't change the security score.",
  mark_dark: 'Switch to Dark: it stops reading and raises nothing. It shows as a blind spot until switched back on.',
  watching: 'Watching: its data is arriving and it is reading it.',
  waiting: "Waiting for data: switched on, but none of its data sources has sent anything yet, so it can't see anything. It counts as a blind spot until data arrives.",
  blind: 'Blind spot: its data has stopped arriving (nothing in the last 10 minutes), so anything in its area would go unseen.',
  coverage: "Coverage: of the things on your map this WhiteHat should watch, the share its data actually reaches right now. A server with no collector on it, or a network whose traffic log is not read, counts as not seen. 'Nothing on the map to watch' means your map has nothing of that kind, so no number is shown rather than a guess.",
  signals_week: 'Signals: things this WhiteHat noticed in the last 7 days. A person decides whether they become an incident.',
  would_have: "What it would have raised while learning. These alerted no one and don't count anywhere else.",
  data_source: 'Where this WhiteHat gets its data. It sees nothing until one of these is connected and sending.',
  planned: 'Planned: not built yet. It has no data, no signals and nothing to switch on.',
  sample: 'Sample: made-up demo data that comes with WhiteHats, not something from your own systems.',
  take_it_on: 'Take it on: you make this change yourself, in the cloud account. WhiteHats cannot mark it done on your word: it closes the fix when its next read of the account shows the change.',
  apply_fix: 'Apply Fix: Fix Factory writes the change, proves it fixes the weakness without breaking tests, and opens a pull request. Nothing is merged until a person merges it.',
  approve: 'Approve: records your yes. A person then carries out the steps where it says, and marks it done.',
  impact_not_simulated: "Fix Factory hasn't worked on this fix yet, so its effect hasn't been tested.",
  not_affected: 'Not affected: record that this weakness does not apply here, and why. The finding stays listed, marked, and the reason is shared in the standard format suppliers and customers use.',
}
