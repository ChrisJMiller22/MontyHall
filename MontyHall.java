import java.util.Random;

public class MontyHall {

    private static final Random random = new Random();

    public static void main(String[] args) {
        int trials = 100_000;

        int stayWins = 0;
        int switchWins = 0;

        for (int i = 0; i < trials; i++) {
            if (playRound(false)) stayWins++;
            if (playRound(true))  switchWins++;
        }

        System.out.println("Monty Hall Simulation (" + trials + " trials each)");
        System.out.println("--------------------------------------------------");
        System.out.printf("Stay   wins: %d / %d  (%.1f%%)%n", stayWins,   trials, 100.0 * stayWins   / trials);
        System.out.printf("Switch wins: %d / %d  (%.1f%%)%n", switchWins, trials, 100.0 * switchWins / trials);
    }

    /**
     * Simulates one round of the Monty Hall problem.
     * @param switchDoor true = contestant switches after host reveals a goat
     * @return true if the contestant wins the car
     */
    private static boolean playRound(boolean switchDoor) {
        // Randomly place the car behind one of three doors (0, 1, or 2)
        int carDoor = random.nextInt(3);

        // Contestant picks a door at random
        int choice = random.nextInt(3);

        // Host opens a door that is neither the contestant's pick nor the car door
        int hostDoor = -1;
        for (int door = 0; door < 3; door++) {
            if (door != choice && door != carDoor) {
                hostDoor = door;
                break;
            }
        }

        if (switchDoor) {
            // Switch to the remaining unopened door
            for (int door = 0; door < 3; door++) {
                if (door != choice && door != hostDoor) {
                    choice = door;
                    break;
                }
            }
        }

        return choice == carDoor;
    }
}
