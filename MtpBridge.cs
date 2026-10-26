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
                        if (device.FileExists(target) || device.DirectoryExists(target))
                            throw new IOException("Имя уже занято на телефоне. Оригинал сохранён.");
                        string parent = Path.GetDirectoryName(target);
                        if (!device.DirectoryExists(parent)) device.CreateDirectory(parent);
                        string expected;
                        long size;
                        double uploadSeconds, verifySeconds;
                        using (var source = new FileStream(request["source"], FileMode.Open, FileAccess.Read, FileShare.Read))
                        {
                            size = source.Length;
                            using (var sha = SHA256.Create()) expected = Hex(sha.ComputeHash(source));
                            source.Position = 0;
                            var timer = Stopwatch.StartNew();
                            device.UploadFile(source, target);
                            uploadSeconds = timer.Elapsed.TotalSeconds;
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
                        Reply(new { ok = true, sha256 = expected, size = size, upload_seconds = uploadSeconds, verify_seconds = verifySeconds });
                    }
                    else throw new ArgumentException("Unknown command");
                }
              }
              catch (Exception error) { Reply(new { ok = false, error = error.Message }); }
            }
            return 0;
        }
        finally { foreach (var d in devices) d.Dispose(); }
    }
    static string Hex(byte[] hash) { return BitConverter.ToString(hash).Replace("-", "").ToLowerInvariant(); }
    static void Reply(object value) { Console.WriteLine(Json.Serialize(value)); Console.Out.Flush(); }
}
