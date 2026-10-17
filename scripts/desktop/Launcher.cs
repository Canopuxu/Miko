using System;
using System.Diagnostics;
using System.IO;
using System.Collections.Generic;
using System.Windows.Forms;

static class Launcher
{
    [STAThread]
    static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, "app", "python", "pythonw.exe");
        string entry = Path.Combine(root, "app", "scripts", "desktop.py");
        try
        {
            if (!File.Exists(python) || !File.Exists(entry))
                throw new IOException("程序文件不完整，请先将整个压缩包解压到文件夹，再打开 Miko.exe。");
            RemoveRetiredFiles(root);
            var start = new ProcessStartInfo(python, "-X utf8 \"" + entry + "\"");
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.EnvironmentVariables.Remove("PYTHONHOME");
            start.EnvironmentVariables.Remove("PYTHONPATH");
            Process.Start(start);
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "Miko 启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    static void RemoveRetiredFiles(string root)
    {
        string app = Path.GetFullPath(Path.Combine(root, "app")) + Path.DirectorySeparatorChar;
        string current = Path.Combine(app, "portable-files.txt");
        string previous = Path.Combine(root, "MikoLocal", "runtime", "desktop-files.txt");
        if (!File.Exists(current) || !File.Exists(previous)) return;
        var keep = new HashSet<string>(File.ReadAllLines(current), StringComparer.OrdinalIgnoreCase);
        var directories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (string relative in File.ReadAllLines(previous))
        {
            if (String.IsNullOrWhiteSpace(relative) || keep.Contains(relative)) continue;
            string target = Path.GetFullPath(Path.Combine(app, relative));
            if (!target.StartsWith(app, StringComparison.OrdinalIgnoreCase))
                throw new IOException("程序文件清单中的路径无效，请重新解压便携包。");
            if (File.Exists(target)) File.Delete(target);
            string parent = Path.GetDirectoryName(target);
            while (parent != null && parent.StartsWith(app, StringComparison.OrdinalIgnoreCase))
            {
                directories.Add(parent);
                parent = Path.GetDirectoryName(parent);
            }
        }
        var ordered = new List<string>(directories);
        ordered.Sort((a, b) => b.Length.CompareTo(a.Length));
        foreach (string directory in ordered)
            if (Directory.Exists(directory) && Directory.GetFileSystemEntries(directory).Length == 0)
                Directory.Delete(directory);
    }
}
