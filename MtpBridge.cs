using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Security.Cryptography;
using System.Web.Script.Serialization;
using System.Collections.Generic;
using System.Diagnostics;
using MediaDevices;

// Small JSON-over-stdio bridge. Source deletion belongs exclusively to the UI worker.
class MtpBridge
{
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    [STAThread]
    static int Main()
    {
        Console.InputEncoding = Encoding.UTF8;
        Console.OutputEncoding = new UTF8Encoding(false);
        var devices = MediaDevice.GetDevices().ToList();
        var knownDirectories = new HashSet<string>(StringComparer.Ordinal);
        try
        {
            string line;
            while ((line = Console.In.ReadLine()) != null)
            {
              try
              {
                var request = Json.Deserialize<Dictionary<string, string>>(line);
                string command = request["command"];
                if (command == "devices")
                    Reply(new { ok = true, devices = devices.Select(d => new { id = d.DeviceId, name = d.FriendlyName ?? d.Description ?? "Телефон" }).ToArray() });
                else
                {
                    var device = devices.SingleOrDefault(d => d.DeviceId == request["device"]);
                    if (device == null) throw new IOException("Телефон отключён. Подключите его заново.");
                    if (!device.IsConnected) device.Connect(enableCache: false);
                    if (command == "directories")
                        Reply(new { ok = true, directories = device.EnumerateDirectories(request["path"]).ToArray() });
                    else if (command == "upload")
                    {
                        string target = request["path"];
                        bool alreadyPresent = device.FileExists(target);
                        if (!alreadyPresent && device.DirectoryExists(target))
                            throw new IOException("Вместо файла на телефоне находится папка. Оригинал сохранён.");
                        string parent = Path.GetDirectoryName(target);
                        string expected;
                        long size;
                        double uploadSeconds, verifySeconds;
                        using (var source = new FileStream(request["source"], FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024, FileOptions.SequentialScan))
                        {
                            size = source.Length;
                            if (alreadyPresent && device.GetFileInfo(target).Length != (ulong)size)
                                throw new IOException("Имя совпадает, но размер копии другой. Оригинал сохранён.");
                            using (var sha = SHA256.Create()) expected = Hex(sha.ComputeHash(source));
                            source.Position = 0;
                            var timer = Stopwatch.StartNew();
                            if (!alreadyPresent)
                            {
                                // Only cache parents confirmed in this connected session. Do not cache files:
                                // Explorer or another application may add a conflicting file at any time.
                                string directoryKey = device.DeviceId + "\n" + parent;
                                if (!knownDirectories.Contains(directoryKey))
                                {
                                    if (!device.DirectoryExists(parent)) device.CreateDirectory(parent);
                                    knownDirectories.Add(directoryKey);
                                }
                                device.UploadFile(source, target);
                            }
                            uploadSeconds = alreadyPresent ? 0 : timer.Elapsed.TotalSeconds;
                            timer.Restart();
                            using (var sha = SHA256.Create())
                            using (var sink = new CryptoStream(Stream.Null, sha, CryptoStreamMode.Write))
                            {
                                // Read bytes back through WPD, without keeping another disk copy.
                                device.DownloadFile(target, sink);
                                sink.FlushFinalBlock();
                                if (Hex(sha.Hash) != expected) throw new IOException("Проверка SHA-256 копии на телефоне не пройдена. Оригинал сохранён.");
                            }
                            verifySeconds = timer.Elapsed.TotalSeconds;
                        }
                        Reply(new { ok = true, sha256 = expected, size = size, already_present = alreadyPresent, upload_seconds = uploadSeconds, verify_seconds = verifySeconds });
                    }
                    else throw new ArgumentException("Unknown command");
                }
              }
              catch (Exception error)
              {
                  string message = error.HResult == unchecked((int)0x800700AA)
                      ? "Телефон занят другой операцией. Дождитесь её завершения и повторите перенос. Оригинал сохранён."
                      : error.Message;
                  Reply(new { ok = false, error = message });
              }
            }
            return 0;
        }
        finally { foreach (var d in devices) d.Dispose(); }
    }
    static string Hex(byte[] hash) { return BitConverter.ToString(hash).Replace("-", "").ToLowerInvariant(); }
    static void Reply(object value) { Console.WriteLine(Json.Serialize(value)); Console.Out.Flush(); }
}
