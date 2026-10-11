' NetGuard tray launcher - used by the scheduled task.
' Starts the tray without a console window flashing.

Set PYTHON to the interpreter you want to use.
If not set, the script falls back to whatever python is on PATH.

Dim shell, fso, here, root, py, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetParentFolderName(here)

If Len(WScript.Arguments) > 0 Then
    py = WScript.Arguments(0)
Else
    py = shell.ExpandEnvironmentStrings("%NETGUARD_PYTHON%")
End If

If Len(py) = 0 Then
    ' try the venv next to the repo, then bare python
    Dim candidates
    candidates = Array(root & "\.venv\Scripts\pythonw.exe", "pythonw.exe", "python.exe")
    Dim i, c
    For i = 0 To UBound(candidates)
        c = candidates(i)
        If i = 0 Then
            If fso.FileExists(c) Then
                py = c
                Exit For
            End If
        Else
            On Error Resume Next
            Dim r
            r = shell.Exec(c & " -c ""pass""")
            If Err.Number = 0 Then
                py = c
                Err.Clear
                Exit For
            End If
            On Error GoTo 0
        End If
    Next
End If

If Len(py) = 0 Then
    MsgBox "NetGuard: no Python interpreter found." & vbCrLf & vbCrLf & _
           "Set NETGUARD_PYTHON to the full path of pythonw.exe.", vbCritical, "NetGuard"
    WScript.Quit 1
End If

cmd = """" & py & """ """ & root & "\tray\nettray.py"""
shell.Run cmd, 0, False