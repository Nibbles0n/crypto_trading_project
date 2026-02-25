#!/usr/bin/env python3
"""
Pine Script Documentation Database Setup
Converts documentation text files to searchable SQLite database
"""

import sqlite3
import json
import re
from pathlib import Path
from typing import List, Dict, Any


class PineDocParser:
    """Parser for Pine Script documentation"""
    
    def __init__(self, doc_file: str):
        self.doc_file = Path(doc_file)
        self.functions = []
        self.syntax_rules = []
    
    def parse_documentation(self) -> Dict[str, Any]:
        """
        Parse the documentation text file
        Expected format:
        
        ## function_name
        Category: indicator/plot/math/etc
        Description: What it does
        Syntax: function_name(param1, param2)
        Parameters:
        - param1: description
        - param2: description
        Returns: what it returns
        Example:
        ```
        code example
        ```
        ---
        """
        content = self.doc_file.read_text()
        
        # Split into function blocks
        function_blocks = re.split(r'\n---+\n', content)
        
        for block in function_blocks:
            func = self._parse_function_block(block.strip())
            if func:
                self.functions.append(func)
        
        # Extract general syntax rules (usually at the start)
        syntax_section = content.split('---')[0] if '---' in content else ''
        if 'syntax' in syntax_section.lower() and not self._parse_function_block(syntax_section):
            self.syntax_rules.append(syntax_section)
        
        return {
            'functions': self.functions,
            'syntax_rules': '\n\n'.join(self.syntax_rules)
        }
    
    def _parse_function_block(self, block: str) -> Dict[str, Any]:
        """Parse a single function documentation block"""
        if not block or len(block) < 10:
            return None
        
        lines = block.split('\n')
        func = {
            'name': '',
            'category': 'general',
            'description': '',
            'syntax': '',
            'parameters': '',
            'returns': '',
            'examples': '',
            'notes': ''
        }
        
        current_field = None
        
        for line in lines:
            line = line.strip()
            
            # Function name (markdown header)
            if line.startswith('##'):
                func['name'] = line.replace('#', '').strip()
                continue
            elif line.startswith('#'):
                func['name'] = line.replace('#', '').strip()
                continue
            
            # Field detection
            lower_line = line.lower()
            if lower_line.startswith('category:'):
                func['category'] = line.split(':', 1)[1].strip()
                current_field = None
            elif lower_line.startswith('description:'):
                func['description'] = line.split(':', 1)[1].strip()
                current_field = 'description'
            elif lower_line.startswith('syntax:'):
                func['syntax'] = line.split(':', 1)[1].strip()
                current_field = 'syntax'
            elif lower_line.startswith('parameters:'):
                current_field = 'parameters'
            elif lower_line.startswith('returns:'):
                func['returns'] = line.split(':', 1)[1].strip()
                current_field = 'returns'
            elif lower_line.startswith('example:') or lower_line.startswith('examples:'):
                current_field = 'examples'
            elif lower_line.startswith('note:') or lower_line.startswith('notes:'):
                current_field = 'notes'
            elif line.startswith('```'):
                # Code block toggle
                continue
            elif current_field and line:
                # Append to current field
                func[current_field] += '\n' + line
        
        # Clean up fields
        for key in func:
            if isinstance(func[key], str):
                func[key] = func[key].strip()
        
        # Only return if we have at least a name
        return func if func['name'] else None


class PineDocDatabase:
    """Create and manage the Pine Script documentation database"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
    
    def create_database(self):
        """Create the database schema"""
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()
        
        # Functions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS functions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                category TEXT,
                description TEXT,
                syntax TEXT,
                parameters TEXT,
                returns TEXT,
                examples TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Syntax rules table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS syntax_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create indexes for fast searching
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_function_name 
            ON functions(name)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_function_category 
            ON functions(category)
        ''')
        
        # Full-text search (FTS5 if available)
        try:
            cursor.execute('''
                CREATE VIRTUAL TABLE IF NOT EXISTS functions_fts 
                USING fts5(name, description, examples, content=functions, content_rowid=id)
            ''')
        except:
            print("Note: FTS5 not available, search will be slower")
        
        self.conn.commit()
    
    def populate_from_parser(self, parser: PineDocParser):
        """Populate database from parsed documentation"""
        cursor = self.conn.cursor()
        
        # Insert functions
        for func in parser.functions:
            cursor.execute('''
                INSERT OR REPLACE INTO functions 
                (name, category, description, syntax, parameters, returns, examples, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                func['name'],
                func['category'],
                func['description'],
                func['syntax'],
                func['parameters'],
                func['returns'],
                func['examples'],
                func['notes']
            ))
        
        # Insert syntax rules
        if parser.syntax_rules:
            cursor.execute('''
                INSERT INTO syntax_rules (content)
                VALUES (?)
            ''', ('\n\n'.join(parser.syntax_rules),))
        
        # Update FTS index if it exists
        try:
            cursor.execute('''
                INSERT INTO functions_fts(functions_fts) VALUES('rebuild')
            ''')
        except:
            pass
        
        self.conn.commit()
        print(f"✓ Inserted {len(parser.functions)} functions")
    
    def add_function(self, func: Dict[str, str]):
        """Add a single function to the database"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO functions 
            (name, category, description, syntax, parameters, returns, examples, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            func.get('name', ''),
            func.get('category', 'general'),
            func.get('description', ''),
            func.get('syntax', ''),
            func.get('parameters', ''),
            func.get('returns', ''),
            func.get('examples', ''),
            func.get('notes', '')
        ))
        self.conn.commit()
    
    def export_to_json(self, output_file: str):
        """Export database to JSON for backup"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM functions')
        functions = [dict(zip([d[0] for d in cursor.description], row)) 
                    for row in cursor.fetchall()]
        
        cursor.execute('SELECT * FROM syntax_rules')
        syntax_rules = [dict(zip([d[0] for d in cursor.description], row)) 
                       for row in cursor.fetchall()]
        
        export = {
            'functions': functions,
            'syntax_rules': syntax_rules
        }
        
        with open(output_file, 'w') as f:
            json.dump(export, f, indent=2)
        
        print(f"✓ Exported to {output_file}")
    
    def import_from_json(self, json_file: str):
        """Import database from JSON"""
        with open(json_file) as f:
            data = json.load(f)
        
        cursor = self.conn.cursor()
        
        # Import functions
        for func in data.get('functions', []):
            cursor.execute('''
                INSERT OR REPLACE INTO functions 
                (name, category, description, syntax, parameters, returns, examples, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                func['name'],
                func['category'],
                func['description'],
                func['syntax'],
                func['parameters'],
                func['returns'],
                func['examples'],
                func['notes']
            ))
        
        # Import syntax rules
        for rule in data.get('syntax_rules', []):
            cursor.execute('''
                INSERT OR REPLACE INTO syntax_rules (id, content)
                VALUES (?, ?)
            ''', (rule['id'], rule['content']))
        
        self.conn.commit()
        print(f"✓ Imported {len(data['functions'])} functions")
    
    def query_stats(self):
        """Print database statistics"""
        cursor = self.conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM functions')
        func_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(DISTINCT category) FROM functions')
        cat_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT category, COUNT(*) FROM functions GROUP BY category')
        categories = cursor.fetchall()
        
        print(f"\nDatabase Statistics:")
        print(f"  Total functions: {func_count}")
        print(f"  Categories: {cat_count}")
        print(f"\nFunctions by category:")
        for cat, count in categories:
            print(f"    {cat}: {count}")
    
    def close(self):
        if self.conn:
            self.conn.close()


def create_sample_documentation():
    """Create a sample documentation file for testing"""
    sample_doc = """
# Pine Script Documentation

This file contains documentation for Pine Script functions and syntax.

---

## plot
Category: plotting
Description: Plots a series of data on the chart. The plot function is one of the most commonly used functions in Pine Script.
Syntax: plot(series, title, color, linewidth, style, trackprice, histbase, offset, join, editable, show_last, display)
Parameters:
- series (series int/float): Series of data to be plotted. Required argument.
- title (const string): Title of the plot.
- color (series color): Color of the plot. Can use constants like color.red or dynamic colors.
- linewidth (input int): Width of the plotted line. Default is 1.
- style (plot_style): Style of the plot. Can be plot.style_line, plot.style_histogram, etc.
Returns: A plot object that can be used with fill() function.
Example:
```pinescript
//@version=5
indicator("Plot Example")
plot(close, title="Close Price", color=color.blue, linewidth=2)
```
Notes: The plot function can only be used in the global scope and cannot be used in local scopes like if statements or loops.

---

## ta.sma
Category: technical_analysis
Description: Calculates the simple moving average of a series over a specified period.
Syntax: ta.sma(source, length)
Parameters:
- source (series int/float): Series of values to process.
- length (simple int): Number of bars (length).
Returns: Simple moving average of source for length bars back.
Example:
```pinescript
//@version=5
indicator("SMA Example")
sma20 = ta.sma(close, 20)
plot(sma20, color=color.red)
```

---

## ta.ema
Category: technical_analysis
Description: Calculates the exponential moving average of a series.
Syntax: ta.ema(source, length)
Parameters:
- source (series int/float): Series of values to process.
- length (simple int): Number of bars (length).
Returns: Exponential moving average of source for length bars back.
Example:
```pinescript
//@version=5
indicator("EMA Example")
ema12 = ta.ema(close, 12)
ema26 = ta.ema(close, 26)
plot(ema12, color=color.blue)
plot(ema26, color=color.red)
```

---

## ta.rsi
Category: technical_analysis
Description: Calculates the Relative Strength Index (RSI).
Syntax: ta.rsi(source, length)
Parameters:
- source (series int/float): Series of values to process.
- length (simple int): Number of bars (length).
Returns: Relative strength index.
Example:
```pinescript
//@version=5
indicator("RSI Example")
rsi14 = ta.rsi(close, 14)
plot(rsi14)
hline(70, "Overbought", color=color.red)
hline(30, "Oversold", color=color.green)
```

---

## indicator
Category: declaration
Description: Sets the indicator properties. Must be called once at the beginning of the script.
Syntax: indicator(title, shorttitle, overlay, format, precision, scale, max_bars_back, timeframe, timeframe_gaps, explicit_plot_zorder, max_lines_count, max_labels_count, max_boxes_count)
Parameters:
- title (const string): The indicator's name. Required.
- shorttitle (const string): The indicator's short title.
- overlay (const bool): If true, the indicator will be overlaid on the main chart. Default is false.
- format (const string): Format of the indicator values (format.inherit, format.price, format.volume).
- precision (const int): Number of decimals to format the indicator value.
Returns: None
Example:
```pinescript
//@version=5
indicator("My Indicator", overlay=true)
plot(close)
```
Notes: This must be the first executable statement in an indicator script.

---

## strategy
Category: declaration
Description: Sets the strategy properties. Must be called once at the beginning of the script.
Syntax: strategy(title, shorttitle, overlay, format, precision, scale, pyramiding, calc_on_order_fills, calc_on_every_tick, max_bars_back, backtest_fill_limits_assumption, default_qty_type, default_qty_value, initial_capital, currency, slippage, commission_type, commission_value, process_orders_on_close, close_entries_rule, margin_long, margin_short, explicit_plot_zorder, max_lines_count, max_labels_count, max_boxes_count, risk_free_rate)
Parameters:
- title (const string): The strategy's name. Required.
- overlay (const bool): If true, the strategy will be overlaid on the main chart.
- pyramiding (const int): Maximum number of entries allowed in the same direction.
- initial_capital (const int/float): Initial capital for strategy simulation. Default is 10000.
Returns: None
Example:
```pinescript
//@version=5
strategy("My Strategy", overlay=true, initial_capital=10000)
if ta.crossover(ta.sma(close, 10), ta.sma(close, 20))
    strategy.entry("Long", strategy.long)
```

---

## strategy.entry
Category: strategy
Description: Generates a strategy order to enter a position.
Syntax: strategy.entry(id, direction, qty, limit, stop, oca_name, oca_type, comment, when, alert_message)
Parameters:
- id (series string): Order identifier. Required.
- direction (strategy_direction): Direction: strategy.long or strategy.short. Required.
- qty (series int/float): Number of contracts/shares/lots/units to trade.
- limit (series int/float): Limit price for the order.
- stop (series int/float): Stop price for the order.
Returns: None
Example:
```pinescript
//@version=5
strategy("Entry Example", overlay=true)
if ta.crossover(close, ta.sma(close, 20))
    strategy.entry("Long", strategy.long)
```

---

## request.security
Category: data
Description: Requests data from another symbol and/or timeframe.
Syntax: request.security(symbol, timeframe, expression, gaps, lookahead, ignore_invalid_symbol, currency)
Parameters:
- symbol (simple string): Symbol to request data from. Required.
- timeframe (simple string): Timeframe of the requested data. Required.
- expression (series int/float/bool/color/string or tuple): Expression to calculate on the requested data. Required.
- gaps (barmerge_gaps): Specifies how gaps in data are handled.
- lookahead (barmerge_lookahead): Controls if data from the future should be used.
Returns: Value of the expression from the requested context.
Example:
```pinescript
//@version=5
indicator("Security Example", overlay=true)
dailyClose = request.security(syminfo.tickerid, "D", close)
plot(dailyClose, "Daily Close", color=color.red)
```
Notes: This function can be computationally expensive. Use with caution.

---
"""
    return sample_doc


def main():
    """Main setup script"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Pine Documentation Database Setup")
    parser.add_argument("--doc-file", help="Path to documentation text file")
    parser.add_argument("--db", default="pine_docs.db", help="Output database path")
    parser.add_argument("--create-sample", action="store_true", 
                       help="Create sample documentation file")
    parser.add_argument("--export-json", help="Export database to JSON")
    parser.add_argument("--import-json", help="Import database from JSON")
    parser.add_argument("--stats", action="store_true", 
                       help="Show database statistics")
    
    args = parser.parse_args()
    
    # Create sample documentation if requested
    if args.create_sample:
        sample_file = "pine_docs_sample.txt"
        Path(sample_file).write_text(create_sample_documentation())
        print(f"✓ Created sample documentation: {sample_file}")
        args.doc_file = sample_file
    
    # Initialize database
    db = PineDocDatabase(args.db)
    
    try:
        # Create schema
        db.create_database()
        print(f"✓ Database created: {args.db}")
        
        # Parse and populate from doc file
        if args.doc_file:
            parser = PineDocParser(args.doc_file)
            parsed_data = parser.parse_documentation()
            db.populate_from_parser(parser)
        
        # Import from JSON
        if args.import_json:
            db.import_from_json(args.import_json)
        
        # Export to JSON
        if args.export_json:
            db.export_to_json(args.export_json)
        
        # Show stats
        if args.stats or args.doc_file or args.import_json:
            db.query_stats()
        
    finally:
        db.close()
    
    print(f"\n✓ Setup complete! Database ready at: {args.db}")


if __name__ == "__main__":
    main()