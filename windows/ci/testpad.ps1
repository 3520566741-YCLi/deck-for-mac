# CI 用的「测试记事本」：云电脑（Windows Server）上自带的 notepad.exe 起不来时，test_hands.py 用它代替。
# 标准 WinForms 窗口：菜单栏（文件 / 编辑 / 查看）、多行文本框；有改动时关窗口会问「保存 / 不保存 / 取消」。
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object System.Windows.Forms.Form
$form.Text = "无标题 - Deck 测试记事本"
$form.Width = 720; $form.Height = 480
$menu = New-Object System.Windows.Forms.MenuStrip
foreach ($pair in @(@("文件(&F)", @("新建(&N)", "打开(&O)...")), @("编辑(&E)", @("撤销(&U)", "全选(&A)")), @("查看(&V)", @("状态栏(&S)")))) {
  $top = New-Object System.Windows.Forms.ToolStripMenuItem($pair[0])
  foreach ($sub in $pair[1]) { [void]$top.DropDownItems.Add($sub) }
  [void]$menu.Items.Add($top)
}
$box = New-Object System.Windows.Forms.TextBox
$box.Multiline = $true; $box.ScrollBars = "Both"; $box.Dock = "Fill"; $box.AcceptsReturn = $true; $box.AcceptsTab = $true
$box.Font = New-Object System.Drawing.Font("Consolas", 12)
$form.Controls.Add($box)
$form.Controls.Add($menu)
$form.MainMenuStrip = $menu
$script:dirty = $false
$box.Add_TextChanged({ $script:dirty = $true; $form.Text = "*无标题 - Deck 测试记事本" })
$form.Add_FormClosing({
  param($s, $e)
  if (-not $script:dirty) { return }
  $dlg = New-Object System.Windows.Forms.Form
  $dlg.Text = "Deck 测试记事本"; $dlg.Width = 380; $dlg.Height = 150; $dlg.StartPosition = "CenterParent"
  $dlg.FormBorderStyle = "FixedDialog"; $dlg.MinimizeBox = $false; $dlg.MaximizeBox = $false
  $lbl = New-Object System.Windows.Forms.Label; $lbl.Text = "你想将更改保存到 无标题 吗？"; $lbl.AutoSize = $true; $lbl.Left = 16; $lbl.Top = 16
  $save = New-Object System.Windows.Forms.Button; $save.Text = "保存"; $save.Left = 40; $save.Top = 60; $save.DialogResult = "Yes"
  $nos = New-Object System.Windows.Forms.Button; $nos.Text = "不保存"; $nos.Left = 140; $nos.Top = 60; $nos.DialogResult = "No"
  $cancel = New-Object System.Windows.Forms.Button; $cancel.Text = "取消"; $cancel.Left = 240; $cancel.Top = 60; $cancel.DialogResult = "Cancel"
  $dlg.Controls.AddRange(@($lbl, $save, $nos, $cancel))
  $r = $dlg.ShowDialog($form)
  if ($r -ne "No") { $e.Cancel = $true }
})
[void]$form.ShowDialog()
