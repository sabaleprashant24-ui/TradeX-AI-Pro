# ==========================================
# position_manager.py
# TradeX AI Pro v3.1
# ==========================================


class PositionManager:

    def __init__(self):

        self.reset()

    # ---------------------------------
    # Reset Position
    # ---------------------------------

    def reset(self):

        self.side = ""

        self.symbol = ""

        self.qty = 0

        self.entry = 0

        self.sl = 0

        self.target1 = 0

        self.target2 = 0

        self.is_active = False

    # ---------------------------------
    # Open Position
    # ---------------------------------

    def open(
        self,
        side,
        symbol,
        qty,
        entry,
        sl,
        target1,
        target2
    ):

        self.side = side

        self.symbol = symbol

        self.qty = qty

        self.entry = entry

        self.sl = sl

        self.target1 = target1

        self.target2 = target2

        self.is_active = True

        print("\n==============================")
        print("POSITION OPENED")
        print("==============================")
        print("SIDE     :", self.side)
        print("SYMBOL   :", self.symbol)
        print("QTY      :", self.qty)
        print("ENTRY    :", self.entry)
        print("SL       :", self.sl)
        print("TARGET 1 :", self.target1)
        print("TARGET 2 :", self.target2)
        print("==============================")

    # ---------------------------------
    # Close Position
    # ---------------------------------

    def close(self):

        print("\n==============================")
        print("POSITION CLOSED")
        print("==============================")

        self.reset()

    # ---------------------------------
    # Position Status
    # ---------------------------------

    def is_open(self):

        return self.is_active


position = PositionManager()