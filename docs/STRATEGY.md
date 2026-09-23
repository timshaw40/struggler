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
