"""Seed the persistent volume, then launch the single service process."""
import os
import uvicorn
from bootstrap import initialize_data

if __name__=='__main__':
    os.environ.setdefault('EXAM_DATA_ROOT',os.getenv('EXAM_DATA_DIR') or '/data')
    initialize_data()
    uvicorn.run('server:app',host='0.0.0.0',port=int(os.getenv('PORT','8000')),workers=1)
