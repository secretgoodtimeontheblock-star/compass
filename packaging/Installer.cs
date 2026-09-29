using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Windows.Forms;

static class Installer
{
    [STAThread]
    static void Main()
    {
        try
        {
            foreach (Process process in Process.GetProcessesByName("Compass"))
            {
                try { process.Kill(); } catch { }
            }
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string dest = Path.Combine(local, "Compass");
            if (Directory.Exists(dest))
                Directory.Delete(dest, true);
            using (Stream resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("App.zip"))
            {
                if (resource == null)
                    throw new InvalidOperationException("\u0412 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u0449\u0438\u043a\u0435 \u043d\u0435\u0442 \u0444\u0430\u0439\u043b\u043e\u0432 \u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u044b.");
                string tmp = Path.Combine(Path.GetTempPath(), "compass-app.zip");
                using (FileStream file = File.Create(tmp))
                    resource.CopyTo(file);
                ZipFile.ExtractToDirectory(tmp, local);
                File.Delete(tmp);
            }
            string exe = Path.Combine(dest, "Compass.exe");
            if (!File.Exists(exe))
                throw new InvalidOperationException("\u041f\u043e\u0441\u043b\u0435 \u0440\u0430\u0441\u043f\u0430\u043a\u043e\u0432\u043a\u0438 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d Compass.exe.");
            Shortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Compass.lnk"), exe, dest);
            string menu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Compass");
            Directory.CreateDirectory(menu);
            Shortcut(Path.Combine(menu, "Compass.lnk"), exe, dest);
            Process.Start(exe);
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "Compass", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    static void Shortcut(string linkPath, string exe, string workDir)
    {
        Type shellType = Type.GetTypeFromProgID("WScript.Shell");
        object shell = Activator.CreateInstance(shellType);
        object link = shellType.InvokeMember("CreateShortcut", System.Reflection.BindingFlags.InvokeMethod, null, shell, new object[] { linkPath });
        Type linkType = link.GetType();
        linkType.InvokeMember("TargetPath", System.Reflection.BindingFlags.SetProperty, null, link, new object[] { exe });
        linkType.InvokeMember("WorkingDirectory", System.Reflection.BindingFlags.SetProperty, null, link, new object[] { workDir });
        linkType.InvokeMember("Description", System.Reflection.BindingFlags.SetProperty, null, link, new object[] { "Compass" });
        linkType.InvokeMember("Save", System.Reflection.BindingFlags.InvokeMethod, null, link, null);
    }
}
