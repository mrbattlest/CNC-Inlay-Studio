- Surfacing-bit dropdown is now driven by the bits you've actually
  picked for the cut: dedicated surfacing bits + whichever roughing
  endmills are checked on either side. Deselecting a roughing bit
  immediately rebuilds the dropdown and auto-falls-back to the
  largest remaining candidate, so the Surfacing tab stays populated
  without forcing a re-pick.
- Settings → Surface plug backing → 💾 Save now redraws the
  Surfacing tab's 3D preview right away. Bit, panel dimensions,
  depth-to-remove, pass depth, RPM, and feed all flow through to
  the canvas the moment you save, so you can see the saved plan
  reflected without having to re-open the dialog or re-Preview.
