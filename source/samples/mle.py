# Dùng đề giới hạn 64 MiB để demo OOM của Docker, không chỉ lỗi cấp phát của JVM.
blocks = []
while True:
    blocks.append(bytearray(8 * 1024 * 1024))
