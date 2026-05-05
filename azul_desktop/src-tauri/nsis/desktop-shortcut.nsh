!macro NSIS_HOOK_POSTINSTALL
  CreateShortCut "$DESKTOP\AzulClaw.lnk" "$INSTDIR\AzulClaw.exe"
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  Delete "$DESKTOP\AzulClaw.lnk"
!macroend
