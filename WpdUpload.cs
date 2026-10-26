using System;
using System.IO;
using System.Reflection;
using System.Runtime.ExceptionServices;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using MediaDevices;

// WPD creates a NEW object and returns its ID. No path lookup is needed to verify it.
// Interop metadata is reused from the pinned MediaDevices 1.10.2 assembly.
static class WpdUpload
{
    const BindingFlags Instance = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
    const BindingFlags Static = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
    static readonly Assembly Library = typeof(MediaDevice).Assembly;

    static object Call(Type type, object owner, string method, object[] args)
    {
        try { return type.GetMethod(method, Instance).Invoke(owner, args); }
        catch (TargetInvocationException error)
        {
            ExceptionDispatchInfo.Capture(error.InnerException ?? error).Throw();
            throw;
        }
    }

    public static MediaFileInfo Create(MediaDevice device, string parentId, string name, Stream source)
    {
        var valuesType = Library.GetType("MediaDevices.Internal.IPortableDeviceValues", true);
        var keys = Library.GetType("MediaDevices.Internal.WPD", true);
        object values = Activator.CreateInstance(Library.GetType("MediaDevices.Internal.PortableDeviceValues", true));
        IStream output = null;
        IntPtr written = Marshal.AllocHGlobal(4);
        try
        {
            foreach (var pair in new[] { new[] { "OBJECT_PARENT_ID", parentId }, new[] { "OBJECT_NAME", name }, new[] { "OBJECT_ORIGINAL_FILE_NAME", name } })
                Call(valuesType, values, "SetStringValue", new[] { keys.GetField(pair[0], Static).GetValue(null), (object)pair[1] });
            Call(valuesType, values, "SetUnsignedLargeIntegerValue", new[] { keys.GetField("OBJECT_SIZE", Static).GetValue(null), (object)(ulong)source.Length });
            object content = typeof(MediaDevice).GetField("deviceContent", Instance).GetValue(device);
            object[] create = { values, null, (uint)0, null };
            Call(Library.GetType("MediaDevices.Internal.IPortableDeviceContent", true), content, "CreateObjectWithPropertiesAndData", create);
            output = (IStream)create[1];
            uint recommended = (uint)create[2];
            int bufferSize = recommended == 0 ? 1024 * 1024 : (int)Math.Min(4 * 1024 * 1024, Math.Max(4096, recommended));
            byte[] buffer = new byte[bufferSize];
            int count;
            while ((count = source.Read(buffer, 0, buffer.Length)) != 0)
            {
                Marshal.WriteInt32(written, 0);
                output.Write(buffer, count, written);
                if (Marshal.ReadInt32(written) != count)
                    throw new IOException("Телефон записал не все байты. Оригинал сохранён.");
            }
            output.Commit(0);
            string id;
            ((IPortableDeviceDataStream)output).GetObjectID(out id);
            if (String.IsNullOrEmpty(id)) throw new IOException("Телефон не вернул ID созданного файла. Оригинал сохранён.");
            var itemType = Library.GetType("MediaDevices.Internal.Item", true);
            object item = Activator.CreateInstance(itemType, Instance, null, new object[] { device, id }, null);
            return (MediaFileInfo)Activator.CreateInstance(typeof(MediaFileInfo), Instance, null, new[] { (object)device, item }, null);
        }
        finally
        {
            Marshal.FreeHGlobal(written);
            if (output != null && Marshal.IsComObject(output)) Marshal.ReleaseComObject(output);
            if (Marshal.IsComObject(values)) Marshal.ReleaseComObject(values);
        }
    }

    [ComImport, Guid("88E04DB3-1012-4D64-9996-F703A950D3F4"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPortableDeviceDataStream : IStream
    {
        new void Read([Out, MarshalAs(UnmanagedType.LPArray, SizeParamIndex = 1)] byte[] buffer, int count, IntPtr read);
        new void Write([MarshalAs(UnmanagedType.LPArray, SizeParamIndex = 1)] byte[] buffer, int count, IntPtr written);
        new void Seek(long offset, int origin, IntPtr position);
        new void SetSize(long size);
        new void CopyTo(IStream stream, long count, IntPtr read, IntPtr written);
        new void Commit(int flags);
        new void Revert();
        new void LockRegion(long offset, long count, int type);
        new void UnlockRegion(long offset, long count, int type);
        new void Stat(out System.Runtime.InteropServices.ComTypes.STATSTG stat, int flags);
        new void Clone(out IStream stream);
        void GetObjectID([MarshalAs(UnmanagedType.LPWStr)] out string id);
        void Cancel();
    }
}
