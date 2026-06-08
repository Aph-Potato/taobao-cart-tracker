Set WShell = CreateObject("WScript.Shell")
WShell.CurrentDirectory = "C:\Users\xiaomi\taobao-price-tracker"
WShell.Run "pythonw run.py", 0, False
