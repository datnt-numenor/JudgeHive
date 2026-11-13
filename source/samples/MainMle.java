// Nộp với tên Main.java vào đề RAM 64 MiB: JVM hết heap, worker xếp MLE.
import java.util.ArrayList;
public class Main {
    public static void main(String[] args) {
        ArrayList<long[]> blocks = new ArrayList<>();
        while (true) {
            blocks.add(new long[1 << 20]);
        }
    }
}
