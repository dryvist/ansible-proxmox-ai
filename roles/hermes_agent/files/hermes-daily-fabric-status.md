Use only the tools this job grants you. Never create, write, save, or execute
anything of your own to produce a number: no helper file, no temporary file, no
one-liner program, and never ask the shell to run one. Count what a tool
returned by reading its own output. If a check needs a tool, a credential, or a
source you do not have, skip that check, name it as unavailable in your report,
and carry on: a missing source never ends the run, and your reply always ends
with its last line.

Budget: at most 12 tool calls for the whole run. Probe each source once; never
re-probe a source you already probed, and never spend a call on a check the
list below does not name.

Report shape, exactly: at most 15 lines of plain text, no Markdown tables and
no preamble — one headline line, then one line per finding with the value and
the tool call that produced it, then the board line, then your final line. A
source that failed, timed out, or answered nothing is a finding: name it, say
what came back, and mark it unavailable. Never drop the final line because a
source failed, and never replace a value you did not observe with a guess.

What to check, in this order: LLM router health; Hermes gateway health;
Hindsight memory-pool health; merge-ready pull requests; the board counts the
block below asks for. The block below names every address to probe — probe
exactly those and nothing else, and take the card counts from the kanban tool's
own answer.

Delta discipline: recall your last posted fingerprint and its timestamp from
memory (key "fabric-status-last"). If today's status is unchanged from that
fingerprint AND it is under 24h old, reply with the one-line update "No change
since <timestamp> — still <short status>." and save nothing new. Otherwise
post the full report above, and save today's fingerprint and timestamp to
memory under that key.
