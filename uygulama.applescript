-- PDF Küçült — sürükle-bırak Mac uygulaması.
-- PDF dosyalarını veya klasörleri simgenin üzerine bırakın ya da çift tıklayıp seçin.
-- Dosya -> yanına "ad (küçük).pdf"; klasör -> yanına "klasör (küçültülmüş)" (alt klasörlerle birlikte).
-- Orijinal dosyalara asla dokunulmaz.


on clearQuarantine()
	-- Kullanıcı uygulamayı bir kez "Yine de Aç" ile onayladıktan sonra, içindeki motorun da
	-- (ör. Zotero eklentisi çağırdığında) engellenmemesi için indirme işaretini kaldır.
	try
		do shell script "xattr -dr com.apple.quarantine " & quoted form of POSIX path of (path to me) & " 2>/dev/null; true"
	end try
end clearQuarantine

on run
	clearQuarantine()
	set choice to button returned of (display dialog "PDF dosyalarını veya klasörleri bu uygulamanın simgesine sürükleyip bırakabilirsiniz." & return & return & "Şimdi ne seçmek istersiniz?" with title "PDF Küçült" buttons {"Vazgeç", "Klasör…", "PDF Dosyaları…"} default button 3 cancel button 1)
	if choice is "Klasör…" then
		set picked to choose folder with prompt "Küçültülecek PDF'lerin bulunduğu klasörü seçin (alt klasörler de dahil):" with multiple selections allowed
	else
		set picked to choose file with prompt "Küçültülecek PDF dosyalarını seçin:" of type {"com.adobe.pdf"} with multiple selections allowed
	end if
	processItems(picked)
end run

on open droppedItems
	clearQuarantine()
	processItems(droppedItems)
end open

on processItems(theItems)
	set args to ""
	set firstPath to ""
	repeat with f in theItems
		set p to POSIX path of f
		if firstPath is "" then set firstPath to p
		set args to args & " " & quoted form of p
	end repeat

	-- Paketlenmiş motor (Contents/Resources/motor/<arm64|x86_64>/pdfkucult); yoksa geliştirme ortamı
	set engineCmd to do shell script "r=" & quoted form of (POSIX path of (path to me) & "Contents/Resources") & "; e=\"$r/motor/$(uname -m)/pdfkucult\"; if [ -x \"$e\" ]; then printf '%q --motor' \"$e\"; else s=\"$HOME/Library/Application Support/PDFKucult\"; printf '%q -I %q' \"$s/venv/bin/python\" \"$r/motor.py\"; fi"
	set statusFile to do shell script "mktemp -t pdfkucult"

	set cmd to "export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH; " & engineCmd & " --durum " & quoted form of statusFile & args & " > " & quoted form of (statusFile & ".log") & " 2>&1 & echo $!"
	set pid to do shell script cmd

	set progress description to "Akıllı sıkıştırma yapılıyor…"
	set progress additional description to "Dosyalar taranıyor…"
	set progress total steps to -1
	set progress completed steps to 0
	set reportPath to ""
	set finished to false
	try
		repeat while not finished
			delay 0.4
			set statusText to ""
			try
				set statusText to read POSIX file statusFile as «class utf8»
			end try
			if statusText is not "" then
				repeat with lineRef in paragraphs of statusText
					set ln to lineRef as text
					if ln starts with "T" & tab then
						set progress total steps to (text 3 thru -1 of ln) as integer
					else if ln starts with "D" & tab then
						set AppleScript's text item delimiters to tab
						set parts to text items of ln
						set AppleScript's text item delimiters to ""
						set progress completed steps to (item 2 of parts) as integer
						set progress additional description to (item 2 of parts) & " / " & (item 3 of parts) & " tamamlandı — " & (item 4 of parts)
					else if ln starts with "S" & tab then
						set reportPath to text 3 thru -1 of ln
						set finished to true
					end if
				end repeat
			end if
			if not finished then
				set alive to do shell script "kill -0 " & pid & " 2>/dev/null && echo 1 || echo 0"
				if alive is "0" then
					delay 0.5
					try
						set statusText to read POSIX file statusFile as «class utf8»
						if statusText contains (linefeed & "S" & tab) or statusText starts with ("S" & tab) then
							set finished to true
						end if
					end try
					if not finished then
						set errText to ""
						try
							set errText to do shell script "tail -n 15 " & quoted form of (statusFile & ".log")
						end try
						display dialog "Beklenmeyen bir hata oluştu:" & return & return & errText with title "PDF Küçült" buttons {"Tamam"} default button 1 with icon stop
						return
					end if
				end if
			end if
		end repeat
	on error number -128
		-- Kullanıcı ilerleme penceresinde "Durdur"a bastı
		do shell script "pkill -TERM -P " & pid & " 2>/dev/null; kill -TERM " & pid & " 2>/dev/null; true"
		display dialog "İşlem durduruldu. Tamamlanan dosyalar yerinde duruyor; orijinallere dokunulmadı." with title "PDF Küçült" buttons {"Tamam"} default button 1
		return
	end try

	set summary to ""
	try
		set summary to read POSIX file (statusFile & ".ozet") as «class utf8»
	end try
	do shell script "rm -f " & quoted form of statusFile & " " & quoted form of (statusFile & ".ozet") & " " & quoted form of (statusFile & ".log")

	if reportPath is "" then
		display dialog summary with title "PDF Küçült" buttons {"Tamam"} default button 1
		return
	end if
	set btn to button returned of (display dialog summary with title "PDF Küçült — tamamlandı" buttons {"Raporu Aç", "Finder'da Göster", "Tamam"} default button 3)
	if btn is "Raporu Aç" then
		do shell script "open -e " & quoted form of reportPath
	else if btn is "Finder'da Göster" then
		-- klasör bırakıldıysa (küçültülmüş) klasörünü, dosya bırakıldıysa ilk çıktıyı göster
		set revealPath to do shell script "p=" & quoted form of firstPath & "; p=${p%/}; if [ -d \"$p\" ]; then echo \"$p (küçültülmüş)\"; else echo \"${p%.*} (küçük).pdf\"; fi"
		do shell script "open -R " & quoted form of revealPath & " 2>/dev/null || open " & quoted form of (do shell script "dirname " & quoted form of revealPath)
	end if
end processItems
