using System;
using System.Diagnostics;
using System.IO;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

static class Launcher
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetInformationJobObject(IntPtr job, int kind, ref JobLimits value, uint length);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [StructLayout(LayoutKind.Sequential)]
    struct BasicLimits
    {
        public long ProcessTime, JobTime;
        public uint Flags;
        public UIntPtr MinWorking, MaxWorking;
        public uint ActiveLimit;
        public UIntPtr Affinity;
        public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct JobLimits
    {
        public BasicLimits Basic;
        public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    static IntPtr job;

    [STAThread]
    static void Main()
    {
        string root = Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory);
        string local = Path.Combine(root, "MikoLocal");
        string key;
        using (var hash = SHA256.Create())
            key = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(local.ToLowerInvariant()))).Replace("-", "").ToLowerInvariant().Substring(0, 24);
        bool first;
        using (var mutex = new Mutex(false, "Local\\Miko-launcher-" + key, out first))
        using (var wake = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\Miko-wake-" + key))
        {
            if (!first) { wake.Set(); return; }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            var form = new Form { Text = "Miko", Width = 390, Height = 150,
                FormBorderStyle = FormBorderStyle.FixedDialog, MaximizeBox = false,
                StartPosition = FormStartPosition.CenterScreen, BackColor = System.Drawing.Color.FromArgb(248, 247, 243) };
            var label = new Label { Text = "正在检查程序文件…", Left = 24, Top = 22, Width = 340, Height = 30 };
            var bar = new ProgressBar { Style = ProgressBarStyle.Marquee, Left = 24, Top = 64, Width = 325, Height = 6 };
            form.Controls.Add(label); form.Controls.Add(bar);
            string stateFile = Path.Combine(local, "runtime", "desktop-startup.txt");
            bool finished = false;
            var timer = new System.Windows.Forms.Timer { Interval = 200 };
            timer.Tick += delegate {
                try {
                    if (File.Exists(stateFile)) {
                        string state = File.ReadAllText(stateFile, Encoding.UTF8);
                        if (state == "READY" || state == "ERROR") form.Hide();
                        else if (state.Length > 0) label.Text = state;
                    }
                    if (finished) form.Close();
                } catch (IOException) { }
            };
            form.Shown += delegate {
                timer.Start();
                new Thread(delegate() {
                    try {
                        Directory.CreateDirectory(Path.Combine(local, "logs"));
                        Directory.CreateDirectory(Path.Combine(local, "runtime"));
                        if (File.Exists(stateFile)) File.Delete(stateFile);
                        CheckSystem();
                        VerifyFiles(root);
                        // Join before spawning Python: its entire process tree inherits
                        // this job, including during startup and backup restoration.
                        job = CreateJobObject(IntPtr.Zero, null);
                        var limits = new JobLimits(); limits.Basic.Flags = 0x2000;
                        if (job == IntPtr.Zero || !SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(limits)) ||
                            !AssignProcessToJobObject(job, Process.GetCurrentProcess().Handle))
                            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                        string python = Path.Combine(root, "app", "python", "python.exe");
                        string entry = Path.Combine(root, "app", "scripts", "desktop.py");
                        var start = new ProcessStartInfo(python, "-B -X utf8 \"" + entry + "\"");
                        start.WorkingDirectory = root;
                        start.UseShellExecute = false; start.CreateNoWindow = true;
                        start.RedirectStandardOutput = true; start.RedirectStandardError = true;
                        start.EnvironmentVariables.Remove("PYTHONHOME"); start.EnvironmentVariables.Remove("PYTHONPATH");
                        start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
                        using (var log = new StreamWriter(Path.Combine(local, "logs", "desktop-bootstrap.log"), true, Encoding.UTF8)) {
                            object logLock = new object();
                            var child = new Process { StartInfo = start };
                            DataReceivedEventHandler collect = delegate(object sender, DataReceivedEventArgs data) {
                                if (data.Data != null) lock (logLock) { log.WriteLine(data.Data); log.Flush(); }
                            };
                            child.OutputDataReceived += collect; child.ErrorDataReceived += collect;
                            child.Start(); child.BeginOutputReadLine(); child.BeginErrorReadLine(); child.WaitForExit();
                            if (child.ExitCode != 0 && (!File.Exists(stateFile) || File.ReadAllText(stateFile, Encoding.UTF8) != "ERROR"))
                                throw new IOException("Miko 启动未完成。请重新解压完整便携包；详细原因见 MikoLocal\\logs\\desktop-bootstrap.log。");
                        }
                    } catch (Exception error) {
                        MessageBox.Show(error.Message, "Miko 启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    } finally { finished = true; }
                }) { IsBackground = true }.Start();
            };
            Application.Run(form);
            timer.Dispose();
            GC.KeepAlive(job);
        }
    }

    static void CheckSystem()
    {
        using (var key = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows NT\CurrentVersion")) {
            int build;
            if (!Environment.Is64BitOperatingSystem || key == null ||
                !Int32.TryParse(Convert.ToString(key.GetValue("CurrentBuildNumber")), out build) || build < 10240)
                throw new IOException("Miko 便携版需要 Windows 10 或 Windows 11 64 位系统。");
        }
        using (var key = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full")) {
            if (key == null || Convert.ToInt32(key.GetValue("Release", 0)) < 394802) {
                if (MessageBox.Show("需要 Microsoft .NET Framework 4.6.2 或更高版本。现在打开微软安装页面吗？", "Miko", MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
                    Process.Start(new ProcessStartInfo("https://dotnet.microsoft.com/download/dotnet-framework/net48") { UseShellExecute = true });
                throw new IOException("安装 .NET Framework 后，再次打开 Miko。");
            }
        }
    }

    static void VerifyFiles(string root)
    {
        string app = Path.GetFullPath(Path.Combine(root, "app")) + Path.DirectorySeparatorChar;
        string inventory = Path.Combine(app, "portable-files.sha256");
        string fileList = Path.Combine(app, "portable-files.txt");
        if (!File.Exists(inventory) || !File.Exists(fileList)) throw new IOException("程序文件不完整，请重新解压整个便携包。");
        var expected = new HashSet<string>(File.ReadAllLines(fileList, Encoding.UTF8), StringComparer.OrdinalIgnoreCase);
        expected.Remove("portable-files.sha256");
        string[] rows = File.ReadAllLines(inventory, Encoding.UTF8);
        foreach (string row in rows) {
            string[] pair = row.Split(new char[] {'\t'}, 2);
            if (pair.Length != 2 || !expected.Remove(pair[1])) throw new IOException("程序文件清单损坏，请重新解压整个便携包。");
            string file = Path.GetFullPath(Path.Combine(app, pair[1]));
            if (!file.StartsWith(app, StringComparison.OrdinalIgnoreCase) || !File.Exists(file))
                throw new IOException("程序文件缺失：" + pair[1] + "\n请退出 Miko 后，重新解压整个便携包。");
            using (var hash = SHA256.Create())
            using (var stream = File.OpenRead(file)) {
                string actual = BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                if (!String.Equals(actual, pair[0], StringComparison.OrdinalIgnoreCase))
                    throw new IOException("程序文件不匹配：" + pair[1] + "\n请退出 Miko 后，重新解压整个便携包。");
            }
        }
        if (expected.Count != 0) throw new IOException("程序文件清单不完整，请重新解压整个便携包。");
    }
}
