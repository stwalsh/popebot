import machine
import time

# Wait a few seconds before starting - gives time to interrupt if needed
# Hold down BOOTSEL during this time to prevent auto-start
print("Popebot starting in 5 seconds... (interrupt now if needed)")
time.sleep(5)

try:
    import popebot
    popebot.main()
except Exception as e:
    print(f"Bot crashed: {e}")
    # Blink LED rapidly to indicate error
    led = machine.Pin("LED", machine.Pin.OUT)
    for _ in range(20):
        led.toggle()
        time.sleep(0.1)
    # Don't auto-restart - wait for manual intervention
    print("Bot stopped. Reset device to retry.")
