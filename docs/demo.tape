# Regenerate the README GIF:  vhs docs/demo.tape
Output docs/demo.gif
Set Shell zsh
Set FontSize 15
Set Width 1300
Set Height 470
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms
Hide
Type "uv run -q --with rich python -c pass && clear"
Enter
Sleep 3s
Show
Type "uv run --with rich python scripts/demo.py health-fitness tr"
Sleep 400ms
Enter
Wait+Screen@90s /OKOK.*61|OKOK.*%.*reklam/
Sleep 6s
