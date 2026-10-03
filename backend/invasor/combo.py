"""What the open/close combo should do, given the state of every injected window."""


def decide_combo(states):
    """states: [{"role", "open", "focused", "available"}] from each window's __invasor.state().

    - any panel open                     -> "close" (close them all)
    - a focused window where it's offered -> "open"  (open in the focused window only)
    - otherwise                          -> "ignore": no Steam UI on screen (a game in
      front), or the focused window doesn't offer the panel (Quick Access with no game
      running). Opening anyway would leave a panel nobody asked for holding the pad.
    """
    if any(s.get("open") for s in states):
        return "close"
    if any(s.get("focused") and s.get("available", True) for s in states):
        return "open"
    return "ignore"
