from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_perfilusuario_pref_sr_avanzado'),
    ]

    operations = [
        migrations.AddField(
            model_name='perfilusuario',
            name='pref_sr_fractal',
            field=models.BooleanField(default=False),
        ),
    ]
