# Distilled Twilight Struggle heuristics

Source: [Twilight Strategy](https://twilightstrategy.com/) general-strategy
and annotated-games (theory). These are **our** operational rules for
`GreedyPlayer` / the LLM prompt — not a reprint of those articles.

## Opening setup

- **USSR:** 4 East Germany, 4 Poland, 1 Yugoslavia. Overcontrol both
  battlegrounds; Yugoslavia is Italy/Greece access. Do not open Austria.
- **US:** 4 West Germany, 3 Italy. Protects both battlegrounds vs
  Socialist Governments / an Italy coup.

## Turn 1

- **USSR headlines:** Red Scare/Purge, Suez Crisis, Arab-Israeli War,
  Socialist Governments, Vietnam Revolts.
- **USSR AR1:** coup Iran (western Asia access). Italy only if it is
  wide open. Coup with a 4-Ops card (China Card if needed).
- **US:** survive. Jordan/Lebanon for Israel, Malaysia toward Thailand,
  Greece for access. Counter-coup Iran only if their coup was weak;
  prefer the turn’s last coup.

## DEFCON

- You lose if DEFCON hits 1 while **you** are the phasing player (8.1.3) —
  even when the opponent's choice or event is what moved the marker.
- Never play Duck and Cover / We Will Bury You / KAL-007 as the event
  at DEFCON 2. Space or hold opponent suicide cards. CIA Created / Lone
  Gunman / Grain Sales are suicide if the opponent can coup a
  battleground.
- **Their event fires when you play their card for Ops, too.** The engine
  resolves an opponent's event on an Ops play (`Engine._push_play_mode`),
  and never offers their card as a voluntary event — so at DEFCON 2 the
  card must not be committed *at all*, whatever mode is picked. Space Race
  it, or play something else and hold it: the "play it for Ops instead"
  escape that is correct for a neutral or own-side card is not one here.
- **How I Learned to Stop Worrying** offers DEFCON *levels* as its choice,
  and the first one ends the game for the side picking it. Never choose 1;
  above that the level trades the opponent's Military Operations
  requirement (which is the DEFCON level, and this event's own +5 Ops
  covers the phasing side's at any level) against how much Coup freedom
  stays open. Take the highest.

## Events vs Ops

- Ops take countries; events break stalemates. Don’t Space Race every
  opponent card — trigger starred opponent events yourself before Turn 7
  when you can manage them. Space only the unmanageable ones
  (De-Stalinization, Quagmire/Bear Trap, Grain Sales at DEFCON 2).
- **Price your own events, but only where the judgement is unconditional.**
  A card played for its event spends no Ops, so the choice is "the event, or
  `ops` points of Operations". The per-card call lives in the card playbook
  (`bots/llm/card_playbook.json`), which says "Always event" / "Free event"
  for some cards and "Usually better to use for ops" for others — and the
  conditional advice ("Event when they've actually invested in the Middle
  East", "Worthless played late") is deliberately *not* priced, because a
  guess there is worse than the Ops-first default. The two exceptions carry
  their condition: **Containment** and **Brezhnev Doctrine** are
  "first AR of the turn" cards, and the action round is a fact the bot can
  read. A headline resolves as the event and costs no action round, so the
  same table applies there.
- **The Space Race is the way out of a card that is unplayable at DEFCON 2**,
  and there is only one attempt a turn (two with Captured Nazi Scientist).
  So a held suicide card is a countdown: space it, or spend it while the
  marker is still high. At DEFCON 2 the card has to be *chosen* to be spaced
  — the mode decision comes after the card decision — so it is priced at the
  card choice too, and refused there once the attempt is gone. Dropping DEFCON
  to 2 with two such cards in hand is a lost position entered several turns
  earlier, which is what the Coup scoring charges for.
- **Coup when you are behind on Military Operations.** Coups and war events
  pay the requirement, realignments do not, and the shortfall is assessed at
  the end of the turn — so the same Coup is worth more on the last action
  round than on the first. `scoreboard_value` prices that difference.
