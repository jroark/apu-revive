' Guest side of tools/vmlink: polls the host for commands, runs them, sends results back.
' Usage:  cscript //nologo AGENT.VBS [base URL]      (the base URL is filled in on the CD copy)
' Commands: RUN <command line> | PUT <name> <guest path> | SEND <guest path> | QUIT
Option Explicit
Dim base, sh, fso, tmp
base = "@@BASE@@"
If WScript.Arguments.Count > 0 Then base = WScript.Arguments(0)
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
tmp = sh.ExpandEnvironmentStrings("%TEMP%") & "\vmlink_out.txt"

Function NewHttp()
    On Error Resume Next
    Set NewHttp = CreateObject("MSXML2.XMLHTTP")
    If Err.Number <> 0 Then
        Err.Clear
        Set NewHttp = CreateObject("Microsoft.XMLHTTP")
    End If
End Function

Function PostText(url, text)
    Dim x: Set x = NewHttp()
    x.open "POST", url, False
    x.setRequestHeader "Content-Type", "text/plain"
    x.send text
End Function

Function RunCapture(cmdline)
    Dim code, out, f
    If fso.FileExists(tmp) Then fso.DeleteFile tmp
    code = sh.Run("%COMSPEC% /c " & cmdline & " > """ & tmp & """ 2>&1", 0, True)
    out = ""
    If fso.FileExists(tmp) Then
        If fso.GetFile(tmp).Size > 0 Then
            Set f = fso.OpenTextFile(tmp, 1)
            out = f.ReadAll
            f.Close
        End If
    End If
    RunCapture = "exit " & code & vbCrLf & out
End Function

Dim x, text, nl, id, line, verb, rest, sp, st, name, dest, polls
polls = 0
WScript.Echo "vmlink agent polling " & base
Do
    On Error Resume Next
    Set x = NewHttp()
    polls = polls + 1
    ' POST with a unique query: Internet Explorer caches repeated XMLHTTP GETs
    x.open "POST", base & "/next?n=" & polls & "_" & Int(Timer * 100), False
    x.send ""
    If Err.Number <> 0 Then
        Err.Clear
        WScript.Sleep 3000
    Else
        text = x.responseText
        If Len(text) = 0 Then
            WScript.Sleep 1000
        Else
            nl = InStr(text, vbCrLf)
            id = Left(text, nl - 1)
            line = Mid(text, nl + 2)
            sp = InStr(line, " ")
            If sp = 0 Then
                verb = UCase(line): rest = ""
            Else
                verb = UCase(Left(line, sp - 1)): rest = Mid(line, sp + 1)
            End If
            WScript.Echo id & ": " & line
            Select Case verb
            Case "RUN"
                PostText base & "/result/" & id, RunCapture(rest)
            Case "PUT"
                sp = InStr(rest, " ")
                name = Left(rest, sp - 1): dest = Mid(rest, sp + 1)
                Set x = NewHttp()
                x.open "GET", base & "/get/" & name & "?n=" & id, False
                x.send
                If x.status = 200 Then
                    Set st = CreateObject("ADODB.Stream")
                    st.Type = 1: st.Open: st.Write x.responseBody
                    st.SaveToFile dest, 2: st.Close
                    PostText base & "/result/" & id, "saved " & dest & " " & Err.Description
                Else
                    PostText base & "/result/" & id, "PUT failed: HTTP " & x.status
                End If
            Case "SEND"
                If fso.FileExists(rest) Then
                    Set st = CreateObject("ADODB.Stream")
                    st.Type = 1: st.Open: st.LoadFromFile rest
                    Set x = NewHttp()
                    x.open "POST", base & "/file/" & id & "?name=" & fso.GetFileName(rest), False
                    x.setRequestHeader "Content-Type", "application/octet-stream"
                    x.send st.Read
                    st.Close
                Else
                    PostText base & "/result/" & id, "SEND failed: no file " & rest
                End If
            Case "QUIT"
                PostText base & "/result/" & id, "bye"
                WScript.Quit 0
            Case Else
                PostText base & "/result/" & id, "unknown command " & verb
            End Select
            If Err.Number <> 0 Then
                PostText base & "/result/" & id, "error " & Err.Number & ": " & Err.Description
                Err.Clear
            End If
        End If
    End If
Loop
